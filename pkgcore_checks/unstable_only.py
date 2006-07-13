# Copyright: 2006 Brian Harring <ferringb@gmail.com>
# License: GPL2

from pkgcore.restrictions import packages, values
from pkgcore_checks.base import template, package_feed, Result

from pkgcore_checks.arches import default_arches

class UnstableOnlyReport(template):
	"""scan for pkgs that have just unstable keywords"""

	feed_type = package_feed

	def __init__(self, arches=default_arches):
		arches = set(x.strip().lstrip("~") for x in arches)
		# stable, then unstable, then file
		self.arch_restricts = {}
		for x in arches:
			self.arch_restricts[x] = [packages.PackageRestriction("keywords", values.ContainmentMatch(x)),
				packages.PackageRestriction("keywords", values.ContainmentMatch("~%s" % x))]

	def finish(self, reporter):
		self.arch_restricts.clear()

	def feed(self, pkgset, reporter):
		# stable, then unstable, then file
		for k, v in self.arch_restricts.iteritems():
			stable = unstable = None
			for x in pkgset:
				if v[0].match(x):
					stable = x
					break
			if stable is not None:
				continue
			unstable = [x for x in pkgset if v[1].match(x)]
			if unstable:
				reporter.add_report(UnstableOnly(unstable, k))
				

class UnstableOnly(Result):
	description = "package/keywords that are strictly unstable"
	__slots__ = ("category", "package", "version", "arch")
	
	def __init__(self, pkgs, arch):
		self.category, self.package, self.arch = pkgs[0].category, pkgs[0].package, arch
		self.version = tuple(x.fullver for x in pkgs)
	
	def to_str(self):
		return "%s/%s: arch %s, all unstable: [ %s ]" % \
			(self.category, self.package, self.arch, ", ".join(self.version))

	def to_xml(self):
		return \
"""<check name="%s">
	<category>%s</category>
	<package>%s</package>
	<version>%s</version>
	<arch>%s</arch>
	<msg>all versions are unstable</msg>
</check>""" % (self.__class__.__name__, self.category, self.package, 
"</version>\n\t<version>".join(self.version), self.arch)
