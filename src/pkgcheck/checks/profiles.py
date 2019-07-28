from collections import defaultdict
from itertools import filterfalse, chain
import os

from snakeoil.demandload import demandload
from snakeoil.log import suppress_logging
from snakeoil.strings import pluralism as _pl

from .. import base, addons

demandload(
    'snakeoil.contexts:patch',
    'snakeoil.osutils:listdir_dirs,pjoin',
    'snakeoil.sequences:iflatten_instance',
    'pkgcore.ebuild:atom,misc,repo_objs',
    'pkgcore.ebuild:profiles@profiles_mod',
)


class BadProfileEntry(base.Error):
    """Badly formatted entry in a profiles file."""

    __slots__ = ("path", "error")

    threshold = base.repository_feed

    def __init__(self, path, error):
        super().__init__()
        self.path = path
        self.error = error

    @property
    def short_desc(self):
        return f'failed parsing {self.path!r}: {self.error}'


class UnknownProfilePackages(base.Warning):
    """Profile files include package entries that don't exist in the repo."""

    __slots__ = ("path", "packages")

    threshold = base.repository_feed

    def __init__(self, path, packages):
        super().__init__()
        self.path = path
        self.packages = tuple(str(x) for x in packages)

    @property
    def short_desc(self):
        return "%r: unknown package%s: [ %s ]" % (
            self.path, _pl(self.packages), ', '.join(map(repr, self.packages)))


class UnknownProfilePackageUse(base.Warning):
    """Profile files include entries with USE flags that aren't used on any matching packages."""

    __slots__ = ("path", "package", "flags")

    threshold = base.repository_feed

    def __init__(self, path, package, flags):
        super().__init__()
        self.path = path
        self.package = str(package)
        self.flags = tuple(flags)

    @property
    def short_desc(self):
        return "%r: unknown package USE flag%s: [ '%s[%s]' ]" % (
            self.path, _pl(self.flags), self.package,
            ','.join(self.flags))


class UnknownProfileUse(base.Warning):
    """Profile files include USE flags that don't exist."""

    __slots__ = ("path", "flags")

    threshold = base.repository_feed

    def __init__(self, path, flags):
        super().__init__()
        self.path = path
        self.flags = tuple(flags)

    @property
    def short_desc(self):
        return "%r: unknown USE flag%s: [ %s ]" % (
            self.path, _pl(self.flags), ', '.join(map(repr, self.flags)))


class UnknownProfilePackageKeywords(base.Warning):
    """Profile files include package keywords that don't exist."""

    __slots__ = ("path", "package", "keywords")

    threshold = base.repository_feed

    def __init__(self, path, package, keywords):
        super().__init__()
        self.path = path
        self.package = str(package)
        self.keywords = tuple(keywords)

    @property
    def short_desc(self):
        return "%r: unknown package keyword%s: %s: [ %s ]" % (
            self.path, _pl(self.keywords), self.package,
            ', '.join(map(repr, self.keywords)))


class ProfileWarning(base.LogWarning):
    """Badly formatted data in various profile files."""

    threshold = base.repository_feed


class ProfileError(base.LogError):
    """Erroneously formatted data in various profile files."""

    threshold = base.repository_feed


class _ProfileNode(profiles_mod.ProfileNode):
    """Re-inherited to disable instance caching."""


class ProfilesCheck(base.Template):
    """Scan repo profiles for unknown flags/packages."""

    required_addons = (addons.UseAddon,)
    feed_type = base.repository_feed
    scope = base.repository_scope
    known_results = (
        UnknownProfilePackages, UnknownProfilePackageUse, UnknownProfileUse,
        UnknownProfilePackageKeywords, BadProfileEntry, ProfileWarning, ProfileError,
    )

    def __init__(self, options, iuse_handler):
        super().__init__(options)
        self.repo = options.target_repo
        self.profiles_dir = pjoin(self.repo.location, 'profiles')
        self.non_profile_dirs = {
            pjoin(self.profiles_dir, x) for x in addons.ProfileAddon.non_profile_dirs}
        local_iuse = {use for pkg, (use, desc) in self.repo.config.use_local_desc}
        self.available_iuse = frozenset(
            local_iuse | iuse_handler.global_iuse |
            iuse_handler.global_iuse_expand | iuse_handler.unstated_iuse)

        # TODO: move this and the same support in metadata.KeywordsCheck to a shared addon
        special_keywords = {'-*'}
        stable_keywords = self.options.target_repo.known_arches
        unstable_keywords = {'~' + x for x in stable_keywords}
        disabled_keywords = {'-' + x for x in chain(stable_keywords, unstable_keywords)}
        self.valid_keywords = (
            special_keywords | stable_keywords | unstable_keywords | disabled_keywords)

    def feed(self, pkg):
        pass

    def finish(self):
        unknown_pkgs = defaultdict(lambda: defaultdict(list))
        unknown_pkg_use = defaultdict(lambda: defaultdict(list))
        unknown_use = defaultdict(lambda: defaultdict(list))
        unknown_keywords = defaultdict(lambda: defaultdict(list))

        def _pkg_atoms(filename, vals):
            for a in iflatten_instance(vals, atom.atom):
                if not self.repo.match(a):
                    unknown_pkgs[profile.path][filename].append(a)

        def _pkg_keywords(filename, vals):
            for atom, keywords in vals:
                invalid = set(keywords) - self.valid_keywords
                if invalid:
                    unknown_keywords[profile.path][filename].append((atom, invalid))

        def _pkg_use(filename, vals):
            # TODO: give ChunkedDataDict some dict view methods
            d = vals
            if isinstance(d, misc.ChunkedDataDict):
                d = vals.render_to_dict()

            for _pkg, entries in d.items():
                for a, disabled, enabled in entries:
                    pkgs = self.repo.match(a)
                    if not pkgs:
                        unknown_pkgs[profile.path][filename].append(a)
                    else:
                        available = {u for pkg in pkgs for u in pkg.iuse_stripped}
                        unknown_disabled = set(disabled) - available
                        unknown_enabled = set(enabled) - available
                        if unknown_disabled:
                            unknown_pkg_use[profile.path][filename].append(
                                (a, ('-' + u for u in unknown_disabled)))
                        if unknown_enabled:
                            unknown_pkg_use[profile.path][filename].append(
                                (a, unknown_enabled))

        def _use(filename, vals):
            # TODO: give ChunkedDataDict some dict view methods
            d = vals.render_to_dict()
            for _, entries in d.items():
                for _, disabled, enabled in entries:
                    unknown_disabled = set(disabled) - self.available_iuse
                    unknown_enabled = set(enabled) - self.available_iuse
                    if unknown_disabled:
                        unknown_use[profile.path][filename].extend(
                            ('-' + u for u in unknown_disabled))
                    if unknown_enabled:
                        unknown_use[profile.path][filename].extend(
                            unknown_enabled)

        def _deprecated(filename, vals):
            # make sure replacement profile exists
            if vals is not None:
                replacement, msg = vals
                _ProfileNode(pjoin(self.profiles_dir, replacement))

        file_parse_map = {
            'packages': ('packages', _pkg_atoms),
            'package.mask': ('masks', _pkg_atoms),
            'package.unmask': ('unmasks', _pkg_atoms),
            'package.use': ('pkg_use', _pkg_use),
            'package.use.force': ('pkg_use_force', _pkg_use),
            'package.use.stable.force': ('pkg_use_stable_force', _pkg_use),
            'package.use.mask': ('pkg_use_mask', _pkg_use),
            'package.use.stable.mask': ('pkg_use_stable_mask', _pkg_use),
            'use.force': ('use_force', _use),
            'use.stable.force': ('use_stable_force', _use),
            'use.mask': ('use_mask', _use),
            'use.stable.mask': ('use_stable_mask', _use),
            'parent': ('parents', lambda *args: None),
            'deprecated': ('deprecated', _deprecated),

            # non-PMS files
            'package.keywords': ('keywords', _pkg_keywords),
            'package.accept_keywords': ('accept_keywords', _pkg_keywords),
        }

        profile_reports = []
        report_profile_warnings = lambda x: profile_reports.append(ProfileWarning(x))
        report_profile_errors = lambda x: profile_reports.append(ProfileError(x))

        for root, _dirs, files in os.walk(self.profiles_dir):
            if root not in self.non_profile_dirs:
                profile = _ProfileNode(root)
                for f in set(files).intersection(file_parse_map.keys()):
                    attr, func = file_parse_map[f]
                    file_path = pjoin(root[len(self.profiles_dir) + 1:], f)
                    try:
                        # convert log warnings/errors into reports
                        with patch('pkgcore.log.logger.error', report_profile_errors), \
                                patch('pkgcore.log.logger.warning', report_profile_warnings):
                            vals = getattr(profile, attr)
                        func(f, vals)
                    except profiles_mod.ProfileError as e:
                        yield BadProfileEntry(file_path, str(e))

        yield from profile_reports

        for path, filenames in sorted(unknown_pkgs.items()):
            for filename, vals in filenames.items():
                yield UnknownProfilePackages(
                    pjoin(path[len(self.profiles_dir):].lstrip('/'), filename),
                    vals)

        for path, filenames in sorted(unknown_pkg_use.items()):
            for filename, vals in filenames.items():
                for pkg, flags in vals:
                    yield UnknownProfilePackageUse(
                        pjoin(path[len(self.profiles_dir):].lstrip('/'), filename),
                        pkg, flags)

        for path, filenames in sorted(unknown_use.items()):
            for filename, vals in filenames.items():
                yield UnknownProfileUse(
                    pjoin(path[len(self.profiles_dir):].lstrip('/'), filename),
                    vals)

        for path, filenames in sorted(unknown_keywords.items()):
            for filename, vals in filenames.items():
                for pkg, keywords in vals:
                    yield UnknownProfilePackageKeywords(
                        pjoin(path[len(self.profiles_dir):].lstrip('/'), filename),
                        pkg, keywords)


class UnusedProfileDirs(base.Warning):
    """Unused profile directories detected."""

    __slots__ = ("dirs",)

    threshold = base.repository_feed

    def __init__(self, dirs):
        super().__init__()
        self.dirs = tuple(sorted(dirs))

    @property
    def short_desc(self):
        return f"[ {', '.join(self.dirs)} ]"


class ArchesWithoutProfiles(base.Warning):
    """Arches without corresponding profile listings."""

    __slots__ = ("arches",)

    threshold = base.repository_feed

    def __init__(self, arches):
        super().__init__()
        self.arches = arches

    @property
    def short_desc(self):
        return f"[ {', '.join(self.arches)} ]"


class NonexistentProfilePath(base.Warning):
    """Specified profile path in profiles.desc doesn't exist."""

    __slots__ = ("path",)

    threshold = base.repository_feed

    def __init__(self, path):
        super().__init__()
        self.path = path

    @property
    def short_desc(self):
        return self.path


class LaggingProfileEAPI(base.Warning):
    """Profile has an EAPI that is older than one of its parents."""

    __slots__ = ('profile', 'profile_eapi', 'parent', 'parent_eapi')

    threshold = base.repository_feed

    def __init__(self, profile, parent):
        super().__init__()
        self.profile = profile.name
        self.profile_eapi = profile.eapi
        self.parent = parent.name
        self.parent_eapi = parent.eapi

    @property
    def short_desc(self):
        return (
            f'{self.profile!r} profile has EAPI {self.profile_eapi}, '
            f'{self.parent!r} parent has EAPI {self.parent_eapi}'
        )


class UnknownCategories(base.Warning):
    """Category directories that aren't listed in a repo's categories.

    Or the categories of the repo's masters as well.
    """

    __slots__ = ("categories",)

    threshold = base.repository_feed

    def __init__(self, categories):
        super().__init__()
        self.categories = categories

    @property
    def short_desc(self):
        return f"[ {', '.join(self.categories)} ]"


def dir_parents(path):
    """Yield all directory path parents excluding the root directory.

    Example:
    >>> list(dir_parents('/root/foo/bar/baz'))
    ['root/foo/bar', 'root/foo', 'root']
    """
    path = os.path.normpath(path.strip('/'))
    while path:
        yield path
        dirname, _basename = os.path.split(path)
        path = dirname.rstrip('/')


class RepoProfilesCheck(base.Template):
    """Scan repo for various profiles directory issues.

    Including unknown arches in profiles, arches without profiles, and unknown
    categories.
    """

    required_addons = (addons.ProfileAddon,)
    feed_type = base.repository_feed
    scope = base.repository_scope
    known_results = (
        ArchesWithoutProfiles, UnusedProfileDirs, NonexistentProfilePath,
        UnknownCategories, LaggingProfileEAPI,
        ProfileError, ProfileWarning,
    )

    # known profile status types for the gentoo repo
    known_profile_statuses = frozenset(['stable', 'dev', 'exp'])

    def __init__(self, options, profile_addon):
        super().__init__(options)
        self.arches = options.target_repo.known_arches
        self.repo = options.target_repo
        self.profiles_dir = self.repo.config.profiles_base
        self.non_profile_dirs = profile_addon.non_profile_dirs

    def feed(self, pkg):
        pass

    def finish(self):
        category_dirs = set(filterfalse(
            self.repo.false_categories.__contains__,
            (x for x in listdir_dirs(self.repo.location) if x[0] != '.')))
        unknown_categories = category_dirs.difference(self.repo.categories)
        if unknown_categories:
            yield UnknownCategories(unknown_categories)

        arches_without_profiles = self.arches.difference(self.repo.config.profiles.arches())
        if arches_without_profiles:
            yield ArchesWithoutProfiles(arches_without_profiles)

        root_profile_dirs = {'embedded'}
        available_profile_dirs = set()
        for root, _dirs, _files in os.walk(self.profiles_dir):
            d = root[len(self.profiles_dir):].lstrip('/')
            if d:
                available_profile_dirs.add(d)
        available_profile_dirs -= self.non_profile_dirs | root_profile_dirs

        profile_reports = []
        report_profile_warnings = lambda x: profile_reports.append(ProfileWarning(x))
        report_profile_errors = lambda x: profile_reports.append(ProfileError(x))

        # don't check for acceptable profile statuses on overlays
        if self.repo.repo_id == 'gentoo':
            known_profile_statuses = self.known_profile_statuses
        else:
            known_profile_statuses = None

        # forcibly parse profiles.desc and convert log warnings/errors into reports
        profiles_obj = repo_objs.BundledProfiles(self.profiles_dir)
        # TODO: provide easier access to the function underlying a jitted attribute
        arch_profiles = getattr(repo_objs.BundledProfiles, 'arch_profiles').function
        with patch('pkgcore.log.logger.error', report_profile_errors), \
                patch('pkgcore.log.logger.warning', report_profile_warnings):
            profiles = arch_profiles(
                profiles_obj,
                known_status=known_profile_statuses, known_arch=self.arches)

        yield from profile_reports

        seen_profile_dirs = set()
        profile_status = set()
        lagging_profile_eapi = defaultdict(list)
        for path, status in chain.from_iterable(iter(profiles.values())):
            # suppress profile warning/error logs that should be caught by ProfilesCheck
            with suppress_logging():
                try:
                    profile = profiles_mod.ProfileStack(pjoin(self.profiles_dir, path))
                except profiles_mod.ProfileError:
                    yield NonexistentProfilePath(path)
                    continue
                for parent in profile.stack:
                    seen_profile_dirs.update(
                        dir_parents(parent.path[len(self.profiles_dir):]))
                    # flag lagging profile EAPIs -- assumes EAPIs are sequentially
                    # numbered which should be the case for the gentoo repo
                    if (self.repo.repo_id == 'gentoo' and
                            str(profile.eapi) < str(parent.eapi)):
                        lagging_profile_eapi[profile].append(parent)
            profile_status.add(status)

        for profile, parents in lagging_profile_eapi.items():
            yield LaggingProfileEAPI(profile, parents[-1])

        unused_profile_dirs = available_profile_dirs - seen_profile_dirs
        if unused_profile_dirs:
            yield UnusedProfileDirs(unused_profile_dirs)