"""Canonical JSON Scenario Bank loader and integrity checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from profile_agent.schemas.scenario_rag_schema import (
    OfficialDimensionId,
    SCENARIO_ROLE_FAMILY,
    SCENARIO_ROLE_PROFILE_VERSION,
    ScenarioBankManifest,
    ScenarioCard,
    ScenarioConstraint,
    ScenarioModule,
    ScenarioRetrievalUnit,
    ScenarioSourceRecord,
    ScenarioSourceRegistry,
)


DEFAULT_SCENARIO_BANK_ROOT = (
    Path(__file__).resolve().parents[1]
    / "knowledge"
    / "scenario_banks"
    / "ai_application_engineering_2026_h2"
)
OFFICIAL_DIMENSIONS: tuple[str, ...] = tuple(f"role_dim_0{index}" for index in range(1, 7))
_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _json_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"scenario bank file missing: {path.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"scenario bank file is invalid: {path.name}") from exc


def _list_payload(payload: Any, *, path: Path, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError(f"{path.name} must contain a JSON list")


def _load_unique_records(path: Path, model: type[_ModelT], identity_field: str, *keys: str) -> dict[str, _ModelT]:
    payload = _json_payload(path)
    rows = _list_payload(payload, path=path, keys=keys or (identity_field,))
    records: dict[str, _ModelT] = {}
    for index, row in enumerate(rows):
        try:
            record = model.model_validate(row)
        except Exception as exc:
            raise ValueError(f"invalid {identity_field} at {path.name}[{index}]") from exc
        identity = getattr(record, identity_field, None)
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError(f"{identity_field} at {path.name}[{index}] must not be blank")
        if identity in records:
            raise ValueError(f"duplicate {identity_field}: {identity}")
        records[identity] = record
    return records


@dataclass(frozen=True)
class ScenarioCatalog:
    """Immutable in-process view of canonical scenarios, modules and facts."""

    manifest: ScenarioBankManifest
    scenarios: Mapping[str, ScenarioCard]
    modules: Mapping[str, ScenarioModule]
    constraints: Mapping[str, ScenarioConstraint]
    source_registry: Mapping[str, ScenarioSourceRecord]
    as_of: date
    root: Path | None = None

    @classmethod
    def load(
        cls,
        root: Path | str | None = None,
        *,
        as_of: date | None = None,
    ) -> "ScenarioCatalog":
        bank_root = Path(root) if root is not None else DEFAULT_SCENARIO_BANK_ROOT
        if not bank_root.is_dir():
            raise ValueError(f"scenario bank directory missing: {bank_root}")
        resolved_as_of = as_of or date.today()
        if not isinstance(resolved_as_of, date):
            raise TypeError("as_of must be a date")

        manifest_payload = _json_payload(bank_root / "ScenarioBankManifest.json")
        try:
            manifest = ScenarioBankManifest.model_validate(manifest_payload)
        except Exception as exc:
            raise ValueError("invalid ScenarioBankManifest.json") from exc

        scenarios = _load_unique_records(
            bank_root / "scenarios.json",
            ScenarioCard,
            "scenario_id",
            "scenarios",
        )
        modules = _load_unique_records(
            bank_root / "modules.json",
            ScenarioModule,
            "module_id",
            "modules",
        )
        constraints = _load_unique_records(
            bank_root / "constraints.json",
            ScenarioConstraint,
            "constraint_id",
            "constraints",
        )

        source_path = bank_root / "ScenarioSourceRegistry.json"
        if source_path.exists():
            source_payload = _json_payload(source_path)
            if isinstance(source_payload, Mapping) and any(
                key in source_payload for key in ("sources", "records", "entries")
            ):
                source_registry_model = ScenarioSourceRegistry.model_validate(source_payload)
                source_rows = source_registry_model.sources
            else:
                source_rows = [
                    ScenarioSourceRecord.model_validate(row)
                    for row in _list_payload(
                        source_payload,
                        path=source_path,
                        keys=("sources", "records", "entries"),
                    )
                ]
            source_registry: dict[str, ScenarioSourceRecord] = {}
            for source in source_rows:
                if source.source_id in source_registry:
                    raise ValueError(f"duplicate source_id: {source.source_id}")
                source_registry[source.source_id] = source
        else:
            source_registry = {}

        catalog = cls(
            manifest=manifest,
            scenarios=scenarios,
            modules=modules,
            constraints=constraints,
            source_registry=source_registry,
            as_of=resolved_as_of,
            root=bank_root,
        )
        catalog._validate()
        return catalog

    def _validate(self) -> None:
        if self.manifest.role_family != SCENARIO_ROLE_FAMILY:
            raise ValueError("role_family mismatch")
        if self.manifest.role_profile_version != SCENARIO_ROLE_PROFILE_VERSION:
            raise ValueError("role_profile_version mismatch")

        if self.manifest.scenario_count and self.manifest.scenario_count != len(self.scenarios):
            raise ValueError("manifest scenario_count mismatch")
        if self.manifest.retrieval_module_count and self.manifest.retrieval_module_count != len(self.modules):
            raise ValueError("manifest retrieval_module_count mismatch")
        if self.manifest.scenario_ids and set(self.manifest.scenario_ids) != set(self.scenarios):
            raise ValueError("manifest scenario_ids mismatch")
        if self.manifest.module_ids and set(self.manifest.module_ids) != set(self.modules):
            raise ValueError("manifest module_ids mismatch")
        if self.manifest.source_registry_ids and set(self.manifest.source_registry_ids) != set(self.source_registry):
            raise ValueError("manifest source_registry_ids mismatch")

        for scenario in self.scenarios.values():
            if scenario.role_family != self.manifest.role_family:
                raise ValueError(f"scenario {scenario.scenario_id} role_family mismatch")
            if scenario.role_profile_version != self.manifest.role_profile_version:
                raise ValueError(f"scenario {scenario.scenario_id} role_profile_version mismatch")
            for module_id in scenario.modules:
                module = self.modules.get(module_id)
                if module is None:
                    raise ValueError(f"scenario {scenario.scenario_id} references missing module {module_id}")
                if module.scenario_id != scenario.scenario_id:
                    raise ValueError(f"module {module_id} belongs to another scenario")
            for source_id in scenario.source_ids:
                if source_id not in self.source_registry:
                    raise ValueError(f"scenario {scenario.scenario_id} references missing source {source_id}")

        referenced_module_ids: set[str] = set()
        for module in self.modules.values():
            scenario = self.scenarios.get(module.scenario_id)
            if scenario is None:
                raise ValueError(f"module {module.module_id} references missing scenario {module.scenario_id}")
            if module.role_family != self.manifest.role_family:
                raise ValueError(f"module {module.module_id} role_family mismatch")
            if module.role_profile_version != self.manifest.role_profile_version:
                raise ValueError(f"module {module.module_id} role_profile_version mismatch")
            if module.module_id not in scenario.modules:
                raise ValueError(f"module {module.module_id} is not listed by scenario {scenario.scenario_id}")
            referenced_module_ids.add(module.module_id)
            for source_id in module.source_refs:
                if source_id not in self.source_registry:
                    raise ValueError(f"module {module.module_id} references missing source {source_id}")
            for constraint_id in module.constraint_ids:
                constraint = self.constraints.get(constraint_id)
                if constraint is None:
                    raise ValueError(f"module {module.module_id} references missing constraint {constraint_id}")
                if constraint.module_id != module.module_id or constraint.scenario_id != module.scenario_id:
                    raise ValueError(
                        f"constraint {constraint_id} references module {constraint.module_id}, "
                        f"not owning module {module.module_id}"
                    )

        if set(self.modules) != referenced_module_ids:
            raise ValueError("module references are incomplete")

        for constraint in self.constraints.values():
            module = self.modules.get(constraint.module_id)
            scenario = self.scenarios.get(constraint.scenario_id)
            if module is None:
                raise ValueError(f"constraint {constraint.constraint_id} references missing module {constraint.module_id}")
            if scenario is None:
                raise ValueError(f"constraint {constraint.constraint_id} references missing scenario {constraint.scenario_id}")
            if module.scenario_id != constraint.scenario_id:
                raise ValueError(f"constraint {constraint.constraint_id} scenario mismatch")
            if constraint.constraint_id not in module.constraint_ids:
                raise ValueError(f"constraint {constraint.constraint_id} is not listed by module {module.module_id}")
            for source_id in constraint.source_refs:
                if source_id not in self.source_registry:
                    raise ValueError(f"constraint {constraint.constraint_id} references missing source {source_id}")

        self._validate_defaults()
        # Small fixture banks are useful for unit tests.  The frozen release
        # marker (10 scenarios) opts into the six-dimension coverage gate.
        if self.manifest.scenario_count >= 10:
            self._validate_release_coverage()

    def _validate_defaults(self) -> None:
        represented_dimensions = {
            module.primary_dimension_id for module in self.modules.values()
        }
        for dimension_id in represented_dimensions:
            defaults = [
                module
                for module in self.modules.values()
                if module.primary_dimension_id == dimension_id
                and module.default_for_dimension
            ]
            if len(defaults) != 1:
                raise ValueError(
                    f"dimension {dimension_id} must have exactly one default module"
                )
            default = defaults[0]
            if default.status != "active":
                raise ValueError(f"default module {default.module_id} is not active")
            if default.valid_from > self.as_of or (
                default.valid_until is not None and self.as_of > default.valid_until
            ):
                raise ValueError(f"default module {default.module_id} is expired")

    def _validate_release_coverage(self) -> None:
        for dimension_id in OFFICIAL_DIMENSIONS:
            modules = [
                module
                for module in self.modules.values()
                if module.primary_dimension_id == dimension_id
                and module.status == "active"
                and module.valid_from <= self.as_of
                and (module.valid_until is None or self.as_of <= module.valid_until)
            ]
            if len({module.scenario_id for module in modules}) < 4:
                raise ValueError(f"dimension {dimension_id} has fewer than four active scenario entries")
            defaults = [module for module in modules if module.default_for_dimension]
            if len(defaults) != 1:
                raise ValueError(f"dimension {dimension_id} must have exactly one active default module")

    @property
    def active_scenarios(self) -> list[ScenarioCard]:
        return [
            scenario
            for scenario in self.scenarios.values()
            if scenario.status == "active"
            and scenario.valid_from <= self.as_of
            and (scenario.valid_until is None or self.as_of <= scenario.valid_until)
        ]

    @property
    def active_modules(self) -> list[ScenarioModule]:
        return [
            module
            for module in self.modules.values()
            if module.status == "active"
            and module.valid_from <= self.as_of
            and (module.valid_until is None or self.as_of <= module.valid_until)
            and self.scenarios[module.scenario_id].status == "active"
        ]

    def get_scenario(self, scenario_id: str) -> ScenarioCard:
        try:
            return self.scenarios[scenario_id]
        except KeyError as exc:
            raise KeyError(f"unknown scenario_id: {scenario_id}") from exc

    def get_module(self, module_id: str) -> ScenarioModule:
        try:
            return self.modules[module_id]
        except KeyError as exc:
            raise KeyError(f"unknown module_id: {module_id}") from exc

    def get_constraint(self, constraint_id: str) -> ScenarioConstraint:
        try:
            return self.constraints[constraint_id]
        except KeyError as exc:
            raise KeyError(f"unknown constraint_id: {constraint_id}") from exc

    def get_retrieval_unit(self, retrieval_unit_id: str) -> ScenarioRetrievalUnit:
        _scenario, module = self.resolve(retrieval_unit_id)
        return ScenarioRetrievalUnit.from_module(module)

    def resolve(self, retrieval_unit_id: str) -> tuple[ScenarioCard, ScenarioModule]:
        scenario_id, separator, module_id = retrieval_unit_id.partition("::")
        if not separator or not scenario_id or not module_id:
            raise KeyError(retrieval_unit_id)
        module = self.modules.get(module_id)
        scenario = self.scenarios.get(scenario_id)
        if module is None or scenario is None or module.scenario_id != scenario_id:
            raise KeyError(retrieval_unit_id)
        if module.retrieval_unit_id != retrieval_unit_id:
            raise KeyError(retrieval_unit_id)
        return scenario, module

    def constraints_for_module(self, module_id: str) -> list[ScenarioConstraint]:
        module = self.get_module(module_id)
        return [self.constraints[constraint_id] for constraint_id in module.constraint_ids]

    def default_module_for_dimension(self, dimension_id: str) -> ScenarioModule:
        candidates = [
            module
            for module in self.active_modules
            if module.primary_dimension_id == dimension_id and module.default_for_dimension
        ]
        if len(candidates) != 1:
            raise KeyError(f"no unique default module for dimension: {dimension_id}")
        return candidates[0]

    @property
    def manifest_hash(self) -> str:
        """Stable digest of canonical files, useful for index fingerprints."""

        if self.root is None:
            return "sha256:" + hashlib.sha256(
                json.dumps(
                    {
                        "scenarios": [record.model_dump(mode="json") for record in self.scenarios.values()],
                        "modules": [record.model_dump(mode="json") for record in self.modules.values()],
                        "constraints": [record.model_dump(mode="json") for record in self.constraints.values()],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        digest = hashlib.sha256()
        for name in (
            "ScenarioBankManifest.json",
            "scenarios.json",
            "modules.json",
            "constraints.json",
            "ScenarioSourceRegistry.json",
        ):
            path = self.root / name
            if path.exists():
                digest.update(name.encode("utf-8"))
                digest.update(path.read_bytes())
        return "sha256:" + digest.hexdigest()


__all__ = [
    "DEFAULT_SCENARIO_BANK_ROOT",
    "OFFICIAL_DIMENSIONS",
    "ScenarioCatalog",
]
