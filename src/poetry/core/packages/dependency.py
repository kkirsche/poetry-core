import os
import re

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import FrozenSet
from typing import List
from typing import Optional
from typing import Union

from poetry.core.packages.constraints import (
    parse_constraint as parse_generic_constraint,
)
from poetry.core.packages.specification import PackageSpecification
from poetry.core.semver.helpers import parse_constraint
from poetry.core.semver.version_range_constraint import VersionRangeConstraint
from poetry.core.version.markers import parse_marker


if TYPE_CHECKING:
    from poetry.core.packages.constraints import BaseConstraint
    from poetry.core.packages.directory_dependency import DirectoryDependency
    from poetry.core.packages.file_dependency import FileDependency
    from poetry.core.packages.package import Package
    from poetry.core.packages.types import DependencyTypes
    from poetry.core.semver.helpers import VersionTypes
    from poetry.core.version.markers import BaseMarker


class Dependency(PackageSpecification):
    def __init__(
        self,
        name: str,
        constraint: Union[str, "VersionTypes"],
        optional: bool = False,
        groups: Optional[List[str]] = None,
        allows_prereleases: bool = False,
        extras: Union[List[str], FrozenSet[str]] = None,
        source_type: Optional[str] = None,
        source_url: Optional[str] = None,
        source_reference: Optional[str] = None,
        source_resolved_reference: Optional[str] = None,
        source_subdirectory: Optional[str] = None,
    ):
        from poetry.core.version.markers import AnyMarker

        super().__init__(
            name,
            source_type=source_type,
            source_url=source_url,
            source_reference=source_reference,
            source_resolved_reference=source_resolved_reference,
            source_subdirectory=source_subdirectory,
            features=extras,
        )

        self._constraint = None
        self._pretty_constraint = None
        self.set_constraint(constraint=constraint)

        self._optional = optional

        if not groups:
            groups = ["default"]

        self._groups = frozenset(groups)

        if (
            isinstance(self._constraint, VersionRangeConstraint)
            and self._constraint.min
        ):
            allows_prereleases = (
                allows_prereleases or self._constraint.min.is_unstable()
            )

        self._allows_prereleases = allows_prereleases

        self._python_versions = "*"
        self._python_constraint = parse_constraint("*")
        self._transitive_python_versions = None
        self._transitive_python_constraint = None
        self._transitive_marker = None
        self._extras = frozenset(extras or [])

        self._in_extras = []

        self._activated = not self._optional

        self.is_root = False
        self._marker = AnyMarker()
        self.source_name = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def constraint(self) -> "VersionTypes":
        return self._constraint

    def set_constraint(self, constraint: Union[str, "VersionTypes"]) -> None:
        from poetry.core.semver.version_constraint import VersionConstraint

        try:
            if not isinstance(constraint, VersionConstraint):
                self._constraint = parse_constraint(constraint)
            else:
                self._constraint = constraint
        except ValueError:
            self._constraint = parse_constraint("*")
        self._pretty_constraint = str(constraint)

    @property
    def pretty_constraint(self) -> str:
        return self._pretty_constraint

    @property
    def pretty_name(self) -> str:
        return self._pretty_name

    @property
    def groups(self) -> FrozenSet[str]:
        return self._groups

    @property
    def python_versions(self) -> str:
        return self._python_versions

    @python_versions.setter
    def python_versions(self, value: str) -> None:
        self._python_versions = value
        self._python_constraint = parse_constraint(value)
        if not self._python_constraint.is_any():
            self._marker = self._marker.intersect(
                parse_marker(
                    self._create_nested_marker(
                        "python_version", self._python_constraint
                    )
                )
            )

    @property
    def transitive_python_versions(self) -> str:
        if self._transitive_python_versions is None:
            return self._python_versions

        return self._transitive_python_versions

    @transitive_python_versions.setter
    def transitive_python_versions(self, value: str) -> None:
        self._transitive_python_versions = value
        self._transitive_python_constraint = parse_constraint(value)

    @property
    def marker(self) -> "BaseMarker":
        return self._marker

    @marker.setter
    def marker(self, marker: Union[str, "BaseMarker"]) -> None:
        from poetry.core.packages.utils.utils import convert_markers
        from poetry.core.semver.helpers import parse_constraint
        from poetry.core.version.markers import BaseMarker
        from poetry.core.version.markers import parse_marker

        if not isinstance(marker, BaseMarker):
            marker = parse_marker(marker)

        self._marker = marker

        markers = convert_markers(marker)

        if "extra" in markers:
            # If we have extras, the dependency is optional
            self.deactivate()

            for or_ in markers["extra"]:
                for _, extra in or_:
                    self.in_extras.append(extra)

        if "python_version" in markers:
            ors = []
            for or_ in markers["python_version"]:
                ands = []
                for op, version in or_:
                    # Expand python version
                    if op == "==" and "*" not in version:
                        version = "~" + version
                        op = ""
                    elif op == "!=":
                        version += ".*"
                    elif op in ("in", "not in"):
                        versions = []
                        for v in re.split("[ ,]+", version):
                            split = v.split(".")
                            if len(split) in [1, 2]:
                                split.append("*")
                                op_ = "" if op == "in" else "!="
                            else:
                                op_ = "==" if op == "in" else "!="

                            versions.append(op_ + ".".join(split))

                        glue = " || " if op == "in" else ", "
                        if versions:
                            ands.append(glue.join(versions))

                        continue

                    ands.append(f"{op}{version}")

                ors.append(" ".join(ands))

            self._python_versions = " || ".join(ors)
            self._python_constraint = parse_constraint(self._python_versions)

    @property
    def transitive_marker(self) -> "BaseMarker":
        if self._transitive_marker is None:
            return self.marker

        return self._transitive_marker

    @transitive_marker.setter
    def transitive_marker(self, value: "BaseMarker") -> None:
        self._transitive_marker = value

    @property
    def python_constraint(self) -> "VersionTypes":
        return self._python_constraint

    @property
    def transitive_python_constraint(self) -> "VersionTypes":
        if self._transitive_python_constraint is None:
            return self._python_constraint

        return self._transitive_python_constraint

    @property
    def extras(self) -> FrozenSet[str]:
        return self._extras

    @property
    def in_extras(self) -> List[str]:
        return self._in_extras

    @property
    def base_pep_508_name(self) -> str:
        from poetry.core.semver.version import Version
        from poetry.core.semver.version_union import VersionUnion

        requirement = self.pretty_name

        if self.extras:
            extras = ",".join(self.extras)
            requirement += f"[{extras}]"

        if isinstance(self.constraint, VersionUnion):
            if self.constraint.excludes_single_version():
                requirement += f' ({self.constraint})'
            else:
                constraints = self.pretty_constraint.split(",")
                constraints = [parse_constraint(c) for c in constraints]
                constraints = ",".join(str(c) for c in constraints)
                requirement += f" ({constraints})"
        elif isinstance(self.constraint, Version):
            requirement += f" (=={self.constraint.text})"
        elif not self.constraint.is_any():
            requirement += f" ({str(self.constraint).replace(' ', '')})"

        return requirement

    def allows_prereleases(self) -> bool:
        return self._allows_prereleases

    def is_optional(self) -> bool:
        return self._optional

    def is_activated(self) -> bool:
        return self._activated

    def is_vcs(self) -> bool:
        return False

    def is_file(self) -> bool:
        return False

    def is_directory(self) -> bool:
        return False

    def is_url(self) -> bool:
        return False

    def accepts(self, package: "Package") -> bool:
        """
        Determines if the given package matches this dependency.
        """
        return (
            self._name == package.name
            and self._constraint.allows(package.version)
            and (not package.is_prerelease() or self.allows_prereleases())
        )

    def to_pep_508(self, with_extras: bool = True) -> str:
        from poetry.core.packages.utils.utils import convert_markers

        requirement = self.base_pep_508_name

        markers = []
        has_extras = False
        if not self.marker.is_any():
            marker = self.marker
            if not with_extras:
                marker = marker.without_extras()

            # we re-check for any marker here since the without extra marker might
            # return an any marker again
            if not marker.is_empty() and not marker.is_any():
                markers.append(str(marker))

            has_extras = "extra" in convert_markers(marker)
        elif self.python_versions != "*":
            python_constraint = self.python_constraint

            markers.append(
                self._create_nested_marker("python_version", python_constraint)
            )

        in_extras = " || ".join(self._in_extras)
        if in_extras and with_extras and not has_extras:
            markers.append(
                self._create_nested_marker("extra", parse_generic_constraint(in_extras))
            )

        if markers:
            if self.is_vcs() or self.is_url() or self.is_file():
                requirement += " "

            if len(markers) > 1:
                markers = " and ".join(f"({m})" for m in markers)
                requirement += f"; {markers}"
            else:
                requirement += f"; {markers[0]}"

        return requirement

    def _create_nested_marker(
        self, name: str, constraint: Union["BaseConstraint", "VersionTypes"]
    ) -> str:
        from poetry.core.packages.constraints.constraint import Constraint
        from poetry.core.packages.constraints.multi_constraint import MultiConstraint
        from poetry.core.packages.constraints.union_constraint import UnionConstraint
        from poetry.core.semver.version import Version
        from poetry.core.semver.version_union import VersionUnion

        if isinstance(constraint, (MultiConstraint, UnionConstraint)):
            parts = []
            for c in constraint.constraints:
                multi = isinstance(c, (MultiConstraint, UnionConstraint))
                parts.append((multi, self._create_nested_marker(name, c)))

            glue = " and "
            if isinstance(constraint, UnionConstraint):
                parts = [f"({part[1]})" if part[0] else part[1] for part in parts]
                glue = " or "
            else:
                parts = [part[1] for part in parts]

            return glue.join(parts)
        elif isinstance(constraint, Constraint):
            return f'{name} {constraint.operator} "{constraint.version}"'
        elif isinstance(constraint, VersionUnion):
            parts = [self._create_nested_marker(name, c) for c in constraint.ranges]
            glue = " or "
            parts = [f"({part})" for part in parts]

            return glue.join(parts)
        elif isinstance(constraint, Version):
            if constraint.precision >= 3 and name == "python_version":
                name = "python_full_version"

            return f'{name} == "{constraint.text}"'
        else:
            if constraint.min is not None:
                min_name = name
                if constraint.min.precision >= 3 and name == "python_version":
                    min_name = "python_full_version"

                    if constraint.max is None:
                        name = min_name

                op = ">" if not constraint.include_min else ">="
                version = constraint.min.text
                if constraint.max is not None:
                    max_name = name
                    if (
                        constraint.max.precision >= 3
                        and max_name == "python_version"
                    ):
                        max_name = "python_full_version"

                    text = f'{min_name} {op} "{version}"'

                    op = "<="
                    if not constraint.include_max:
                        op = "<"

                    version = constraint.max

                    text += f' and {max_name} {op} "{version}"'

                    return text
            elif constraint.max is not None:
                if constraint.max.precision >= 3 and name == "python_version":
                    name = "python_full_version"

                op = "<" if not constraint.include_max else "<="
                version = constraint.max
            else:
                return ""

            return f'{name} {op} "{version}"'

    def activate(self) -> None:
        """
        Set the dependency as mandatory.
        """
        self._activated = True

    def deactivate(self) -> None:
        """
        Set the dependency as optional.
        """
        if not self._optional:
            self._optional = True

        self._activated = False

    def with_constraint(self, constraint: Union[str, "VersionTypes"]) -> "Dependency":
        new = Dependency(
            self.pretty_name,
            constraint,
            optional=self.is_optional(),
            groups=list(self._groups),
            allows_prereleases=self.allows_prereleases(),
            extras=self._extras,
            source_type=self._source_type,
            source_url=self._source_url,
            source_reference=self._source_reference,
        )

        new.is_root = self.is_root
        new.python_versions = self.python_versions
        new.transitive_python_versions = self.transitive_python_versions
        new.marker = self.marker
        new.transitive_marker = self.transitive_marker

        for in_extra in self.in_extras:
            new.in_extras.append(in_extra)

        return new

    @classmethod
    def create_from_pep_508(
        cls, name: str, relative_to: Optional[Path] = None
    ) -> "DependencyTypes":
        """
        Resolve a PEP-508 requirement string to a `Dependency` instance. If a `relative_to`
        path is specified, this is used as the base directory if the identified dependency is
        of file or directory type.
        """
        from poetry.core.packages.url_dependency import URLDependency
        from poetry.core.packages.utils.link import Link
        from poetry.core.packages.utils.utils import is_archive_file
        from poetry.core.packages.utils.utils import is_installable_dir
        from poetry.core.packages.utils.utils import is_url
        from poetry.core.packages.utils.utils import path_to_url
        from poetry.core.packages.utils.utils import strip_extras
        from poetry.core.packages.utils.utils import url_to_path
        from poetry.core.packages.vcs_dependency import VCSDependency
        from poetry.core.utils.patterns import wheel_file_re
        from poetry.core.vcs.git import ParsedUrl
        from poetry.core.version.requirements import Requirement

        # Removing comments
        parts = name.split(" #", 1)
        name = parts[0].strip()
        if len(parts) > 1:
            rest = parts[1]
            if " ;" in rest:
                name += " ;" + rest.split(" ;", 1)[1]

        req = Requirement(name)

        name = req.name
        path = os.path.normpath(os.path.abspath(name))
        link = None

        if is_url(name):
            link = Link(name)
        elif req.url:
            link = Link(req.url)
        else:
            p, extras = strip_extras(path)
            if os.path.isdir(p) and (os.path.sep in name or name.startswith(".")):

                if not is_installable_dir(p):
                    raise ValueError(
                        f"Directory {name!r} is not installable. File 'setup.py' "
                        "not found."
                    )
                link = Link(path_to_url(p))
            elif is_archive_file(p):
                link = Link(path_to_url(p))

        # it's a local file, dir, or url
        if link:
            is_file_uri = link.scheme == "file"
            is_relative_uri = is_file_uri and re.search(r"\.\./", link.url)

            # Handle relative file URLs
            if is_file_uri and is_relative_uri:
                path = Path(link.path)
                if relative_to:
                    path = relative_to / path
                link = Link(path_to_url(path))

            # wheel file
            version = None
            if link.is_wheel:
                m = wheel_file_re.match(link.filename)
                if not m:
                    raise ValueError(f"Invalid wheel name: {link.filename}")
                name = m.group("name")
                version = m.group("ver")

            name = req.name or link.egg_fragment
            dep = None

            if link.scheme.startswith("git+"):
                url = ParsedUrl.parse(link.url)
                dep = VCSDependency(
                    name,
                    "git",
                    url.url,
                    rev=url.rev,
                    directory=url.subdirectory,
                    extras=req.extras,
                )
            elif link.scheme == "git":
                dep = VCSDependency(
                    name, "git", link.url_without_fragment, extras=req.extras
                )
            elif link.scheme in ["http", "https"]:
                dep = URLDependency(name, link.url)
            elif is_file_uri:
                # handle RFC 8089 references
                path = url_to_path(req.url)
                dep = _make_file_or_dir_dep(
                    name=name, path=path, base=relative_to, extras=req.extras
                )
            else:
                try:
                    # this is a local path not using the file URI scheme
                    dep = _make_file_or_dir_dep(
                        name=name,
                        path=Path(req.url),
                        base=relative_to,
                        extras=req.extras,
                    )
                except ValueError:
                    pass

            if dep is None:
                dep = Dependency(name, version or "*", extras=req.extras)

            if version:
                dep._constraint = parse_constraint(version)
        else:
            constraint = req.constraint if req.pretty_constraint else "*"
            dep = Dependency(name, constraint, extras=req.extras)

        if req.marker:
            dep.marker = req.marker

        return dep

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Dependency):
            return NotImplemented

        return (
            self.is_same_package_as(other)
            and self._constraint == other.constraint
            and self._extras == other.extras
        )

    def __ne__(self, other: Any) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return super().__hash__() ^ hash(self._constraint) ^ hash(self._extras)

    def __str__(self) -> str:
        if self.is_root:
            return self._pretty_name
        return self.base_pep_508_name

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} {self}>'


def _make_file_or_dir_dep(
    name: str,
    path: Path,
    base: Optional[Path] = None,
    extras: Optional[List[str]] = None,
) -> Optional[Union["FileDependency", "DirectoryDependency"]]:
    """
    Helper function to create a file or directoru dependency with the given arguments. If
    path is not a file or directory that exists, `None` is returned.
    """
    from poetry.core.packages.directory_dependency import DirectoryDependency
    from poetry.core.packages.file_dependency import FileDependency

    _path = path
    if not path.is_absolute() and base:
        # a base path was specified, so we should respect that
        _path = Path(base) / path

    if _path.is_file():
        return FileDependency(name, path, base=base, extras=extras)
    elif _path.is_dir():
        return DirectoryDependency(name, path, base=base, extras=extras)

    return None
