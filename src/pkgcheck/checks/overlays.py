from snakeoil.sequences import iflatten_instance
from snakeoil.strings import pluralism as _pl

from . import repo_metadata
from .. import base, addons


class UnusedInMastersLicenses(base.Warning):
    """Licenses detected that are unused in the master repo(s).

    In other words, they're likely to be removed so should be copied to the overlay.
    """

    __slots__ = ("category", "package", "version", "licenses")

    threshold = base.versioned_feed

    def __init__(self, pkg, licenses):
        super().__init__()
        self._store_cpv(pkg)
        self.licenses = tuple(sorted(licenses))

    @property
    def short_desc(self):
        return "unused license%s in master repo(s): %s" % (
            _pl(self.licenses), ', '.join(self.licenses))


class UnusedInMastersMirrors(base.Warning):
    """Mirrors detected that are unused in the master repo(s).

    In other words, they're likely to be removed so should be copied to the overlay.
    """

    __slots__ = ("category", "package", "version", "mirrors")

    threshold = base.versioned_feed

    def __init__(self, pkg, mirrors):
        super().__init__()
        self._store_cpv(pkg)
        self.mirrors = tuple(sorted(mirrors))

    @property
    def short_desc(self):
        return "unused mirror%s in master repo(s): %s" % (
            _pl(self.mirrors), ', '.join(self.mirrors))


class UnusedInMastersEclasses(base.Warning):
    """Eclasses detected that are unused in the master repo(s).

    In other words, they're likely to be removed so should be copied to the overlay.
    """

    __slots__ = ("category", "package", "version", "eclasses")

    threshold = base.versioned_feed

    def __init__(self, pkg, eclasses):
        super().__init__()
        self._store_cpv(pkg)
        self.eclasses = tuple(sorted(eclasses))

    @property
    def short_desc(self):
        return "unused eclass%s in master repo(s): %s" % (
            _pl(self.eclasses, plural='es'), ', '.join(self.eclasses))


class UnusedInMastersGlobalUSE(base.Warning):
    """Global USE flags detected that are unused in the master repo(s).

    In other words, they're likely to be removed so should be copied to the overlay.
    """

    __slots__ = ("category", "package", "version", "flags")

    threshold = base.versioned_feed

    def __init__(self, pkg, flags):
        super().__init__()
        self._store_cpv(pkg)
        self.flags = tuple(sorted(flags))

    @property
    def short_desc(self):
        return "use.desc unused flag%s in master repo(s): %s" % (
            _pl(self.flags), ', '.join(self.flags))


class UnusedInMastersCheck(repo_metadata._MirrorsCheck,
                           base.OverlayRepoCheck, base.ExplicitlyEnabledCheck):
    """Check for various metadata that may be removed from master repos."""

    feed_type = base.versioned_feed
    scope = base.repository_scope
    known_results = (
        UnusedInMastersLicenses, UnusedInMastersMirrors, UnusedInMastersEclasses,
        UnusedInMastersGlobalUSE,
    )

    def start(self):
        self.unused_master_licenses = set()
        self.unused_master_mirrors = set()
        self.unused_master_eclasses = set()
        self.unused_master_flags = set()

        # combine licenses/mirrors/eclasses/flags from all master repos
        for repo in self.options.target_repo.masters:
            self.unused_master_licenses.update(repo.licenses)
            self.unused_master_mirrors.update(repo.mirrors.keys())
            self.unused_master_eclasses.update(repo.eclass_cache.eclasses.keys())
            self.unused_master_flags.update(
                flag for matcher, (flag, desc) in repo.config.use_desc)

        # determine unused licenses/mirrors/eclasses/flags across all master repos
        for repo in self.options.target_repo.masters:
            for pkg in repo:
                self.unused_master_licenses.difference_update(iflatten_instance(pkg.license))
                self.unused_master_mirrors.difference_update(self._get_mirrors(pkg))
                self.unused_master_eclasses.difference_update(pkg.inherited)
                self.unused_master_flags.difference_update(
                    pkg.iuse_stripped.difference(pkg.local_use.keys()))

    def feed(self, pkg):
        # report licenses used in the pkg but not in any pkg from the master repo(s)
        if self.unused_master_licenses:
            pkg_licenses = set(iflatten_instance(pkg.license))
            licenses = self.unused_master_licenses & pkg_licenses
            if licenses:
                yield UnusedInMastersLicenses(pkg, licenses)

        # report mirrors used in the pkg but not in any pkg from the master repo(s)
        if self.unused_master_mirrors:
            pkg_mirrors = self._get_mirrors(pkg)
            mirrors = self.unused_master_mirrors & pkg_mirrors
            if mirrors:
                yield UnusedInMastersMirrors(pkg, mirrors)

        # report eclasses used in the pkg but not in any pkg from the master repo(s)
        if self.unused_master_eclasses:
            pkg_eclasses = set(pkg.inherited)
            eclasses = self.unused_master_eclasses & pkg_eclasses
            if eclasses:
                yield UnusedInMastersEclasses(pkg, eclasses)

        # report global USE flags used in the pkg but not in any pkg from the master repo(s)
        if self.unused_master_flags:
            non_local_use = pkg.iuse_stripped.difference(pkg.local_use.keys())
            flags = self.unused_master_flags.intersection(non_local_use)
            if flags:
                yield UnusedInMastersGlobalUSE(pkg, flags)