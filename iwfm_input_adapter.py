"""Read v4.11 land-use inputs for independent use and opt-in viewer builds."""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
import re
from typing import Mapping

import numpy as np
import pandas as pd
from iwfm_io import parse_iwfm_date, strict_mode
from iwfm_io._parser import IWFMFileReader
from iwfm_io.readers import preprocessor, rootzone


class InputValidationError(ValueError):
    pass


C2VSIMFG_ALIASES = {
    **{f'nonponded:{code}': code for code in (
        'GR', 'CO', 'SB', 'CN', 'DB', 'SA', 'FL', 'AL', 'PA', 'TP',
        'TF', 'CU', 'OG', 'PO', 'TR', 'AP', 'OR', 'CS', 'VI', 'ID')},
    'ponded:rice_fl': 'RI_FL', 'ponded:rice_nfl': 'RI_NFL',
    'ponded:rice_ndc': 'RI_NDC', 'ponded:refuge_sl': 'RF_SL',
    'ponded:refuge_pr': 'RF_PR', 'native:native': 'NV',
    'native:riparian': 'RV', 'urban:urban': 'U',
}

FIXED_SLOTS = {
    'ponded': (
        ('rice_fl', 'Rice: flooded decomposition'),
        ('rice_nfl', 'Rice: nonflooded decomposition'),
        ('rice_ndc', 'Rice: no decomposition'),
        ('refuge_sl', 'Seasonal refuge'), ('refuge_pr', 'Permanent refuge')),
    'native': (('native', 'Native vegetation'), ('riparian', 'Riparian vegetation')),
    'urban': (('urban', 'Urban'),),
}

SOURCE_READERS = {
    'nonponded': ('nonponded_ag', rootzone.read_nonponded_ag_main),
    'ponded': ('ponded_ag', rootzone.read_ponded_ag_main),
    'native': ('native_veg', rootzone.read_native_veg_main),
    'urban': ('urban', rootzone.read_urban_main),
}


@dataclass(frozen=True)
class Category:
    id: str
    source: str
    acreage_column: int
    code: str
    label: str
    identity_basis: str
    legacy_alias: str | None = None


@dataclass(frozen=True)
class UnitBasis:
    model_area_unit: str
    evidence: str

    def __post_init__(self):
        if (self.model_area_unit != 'square_feet' or not isinstance(self.evidence, str)
            or not self.evidence.strip()):
            raise InputValidationError('A verified square_feet unit basis with evidence is required')

    @classmethod
    def from_preprocessor(cls, path: str | Path):
        """Establish model square feet from output units with no length conversion."""
        with strict_mode(True):
            config = preprocessor.read_preprocessor_main(path, follow_references=False).config
        if str(config.get('unitltou', '')).upper() != 'FEET' or config.get('factltou') != 1:
            raise InputValidationError(
                f'{path}: cannot establish model square feet; require UNITLTOU=FEET and FACTLTOU=1')
        return cls('square_feet', f'{Path(path).resolve()}: UNITLTOU=FEET, FACTLTOU=1')


@dataclass
class LandUseSource:
    source: str
    categories: tuple[Category, ...]
    endpoints: tuple[str, ...]
    interval_ends: tuple[datetime, ...]
    coverage_start: datetime
    raw_values: pd.DataFrame
    acres: pd.DataFrame
    input_factor: float
    input_unit: str
    unit_basis: UnitBasis
    component_path: Path
    area_path: Path
    budget_codes: tuple[str, ...]

    def endpoint_for(self, instant: datetime) -> str | None:
        """Use an inclusive caller-supplied start and exclusive IWFM interval ends."""
        if not isinstance(instant, datetime) or instant.tzinfo is not None:
            raise InputValidationError('Lookup instant must be a naive model datetime')
        if instant < self.coverage_start:
            return None
        # At an end boundary the old block has expired; choose the following one.
        position = bisect_right(self.interval_ends, instant)
        return self.endpoints[position] if position < len(self.endpoints) else None

    def values_at(self, instant: datetime) -> pd.DataFrame | None:
        """Return covered acreages, including zero, or None for missing coverage."""
        endpoint = self.endpoint_for(instant)
        if endpoint is None:
            return None
        return self.acres.xs(parse_iwfm_date(endpoint), level='interval_end').copy()


class IWFMInputAdapter:
    def __init__(
        self, rootzone_main: str | Path, *, working_directory: str | Path,
        element_ids, unit_basis: UnitBasis, coverage_start: datetime,
        legacy_aliases: Mapping[str, str] | None = None,
        labels: Mapping[str, str] | None = None,
    ):
        if version('iwfm-io') != '2.15.2':
            raise InputValidationError('This adapter requires iwfm-io==2.15.2')
        self.rootzone_path = Path(rootzone_main).resolve()
        self.working_directory = Path(working_directory).resolve()
        self.element_ids = frozenset(element_ids)
        if not self.element_ids or any(not isinstance(element, int) or element <= 0
                                       for element in self.element_ids):
            raise InputValidationError('Expected nonempty positive integer element IDs')
        if not isinstance(coverage_start, datetime) or coverage_start.tzinfo is not None:
            raise InputValidationError('coverage_start must be a naive model datetime')
        if not isinstance(unit_basis, UnitBasis):
            raise InputValidationError('An explicit verified UnitBasis is required')
        self.unit_basis = unit_basis
        self.coverage_start = coverage_start
        self.legacy_aliases = dict(legacy_aliases or {})
        self.labels = dict(labels or {})
        with strict_mode(True):
            header = IWFMFileReader(self.rootzone_path).read_header()
            if header.version != '4.11':
                raise InputValidationError(
                    f'{self.rootzone_path}: unsupported root-zone format {header.version!r}; supported: 4.11')
            self.rootzone = rootzone.read_rootzone_main(self.rootzone_path)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(source for source, (role, read_component) in SOURCE_READERS.items()
                     if self.rootzone.file_paths.get(role))

    def _path(self, reference, description: str) -> Path:
        if not reference:
            raise InputValidationError(f'{description}: no input file is referenced')
        path = Path(str(reference).replace('\\', '/'))
        if not path.is_absolute():
            path = self.working_directory / path
        if not path.is_file():
            raise InputValidationError(f'{description}: input file unavailable: {path}')
        return path.resolve()

    def read_source(self, source: str) -> LandUseSource:
        """Keep one source's column identities and timeline separate from other sources."""
        if source not in SOURCE_READERS:
            raise InputValidationError(f'Unknown land-use source: {source}')
        role, read_component = SOURCE_READERS[source]
        component_path = self._path(self.rootzone.file_paths.get(role), source)
        with strict_mode(True):
            if source == 'nonponded':
                declaration = IWFMFileReader(component_path)
                declaration.read_header()
                count, keyword = declaration.read_keyed_int()
                declaration.read_keyed_int()
                declared_entries = [declaration.read_keyed_value() for column in range(count)]
                declared_codes = [code for code, description in declared_entries]
                if count <= 0:
                    raise InputValidationError(f'{component_path}: NCROP={count}; require at least one category')
                seen_codes = {}
                for column, code in enumerate(declared_codes, 1):
                    if not code.strip():
                        raise InputValidationError(f'{component_path}: CCODE[{column}] is blank; supply a unique code')
                    if code.casefold() in seen_codes:
                        previous = seen_codes[code.casefold()]
                        raise InputValidationError(
                            f'{component_path}: duplicate category codes {code!r} at CCODE[{previous}] and '
                            f'CCODE[{column}]; use unique codes (case-insensitive)')
                    seen_codes[code.casefold()] = column
                declaration.read_keyed_value()
                budget_count, keyword = declaration.read_keyed_int()
                budget_entries = [declaration.read_keyed_value() for column in range(budget_count)]
                name_candidates = {}
                for code, description in declared_entries + budget_entries:
                    name = re.sub(r'^B?CCODE\s*\[\s*\d+\s*\]\s*', '', description, flags=re.I).strip()
                    if name:
                        name_candidates.setdefault(code.casefold(), set()).add(name)
            component = read_component(component_path, n_elements=len(self.element_ids))
        if source == 'nonponded':
            codes = tuple(component.crop_codes)
            if not codes or len(codes) != component.n_crops:
                raise InputValidationError(f'{component_path}: NCROP and ordered CCODE list disagree')
            default_labels = {}
            for column, code in enumerate(codes, 1):
                names = name_candidates.get(code.casefold(), set())
                default_labels[code] = next(iter(names)) if len(names) == 1 else f'Nonponded crop {column}'
        else:
            codes = tuple(code for code, label in FIXED_SLOTS[source])
            default_labels = dict(FIXED_SLOTS[source])
        if any(not code.strip() for code in codes) or len({code.casefold() for code in codes}) != len(codes):
            raise InputValidationError(f'{component_path}: blank or duplicate category codes')
        categories = tuple(Category(
            id=f'{source}:{code}', source=source, acreage_column=column, code=code,
            label=self.labels.get(f'{source}:{code}', default_labels[code]),
            identity_basis='CCODE' if source == 'nonponded' else 'rootzone_4.11_slot',
            legacy_alias=self.legacy_aliases.get(f'{source}:{code}'),
        ) for column, code in enumerate(codes, 1))
        area_path = self._path(component.file_paths.get('land_use_area'), f'{source} acreage')
        with strict_mode(True):
            area = rootzone.read_land_use_area(area_path, columns=[category.id for category in categories])
        if area.dss_file:
            raise InputValidationError(f'{area_path}: DSS-based acreage input is unsupported')
        if area.repeat_freq != 0:
            raise InputValidationError(f'{area_path}: recurring acreage timelines are unsupported')
        factor = float(area.factor)
        if factor == 0:
            raise InputValidationError(f'{area_path}: FACT=0 fraction conversion is not verified by this adapter')
        if factor not in (1.0, 43560.0):
            raise InputValidationError(f'{area_path}: unsupported acreage FACT={factor}; supported: 1 or 43560 in square feet')
        frame = area.data
        if frame is None or frame.empty:
            raise InputValidationError(f'{area_path}: no inline acreage records')
        endpoints = tuple(frame['date'].unique())
        ends = tuple(parse_iwfm_date(endpoint) for endpoint in endpoints)
        if any(later <= earlier for earlier, later in zip(ends, ends[1:])):
            raise InputValidationError(f'{area_path}: interval endpoints must strictly increase')
        if ends[-1] <= self.coverage_start:
            raise InputValidationError(f'{area_path}: no coverage after the supplied start')
        counts = frame.groupby('date')['element_id'].agg(['count', 'nunique'])
        if (not frame['element_id'].isin(self.element_ids).all()
                or not (counts == len(self.element_ids)).all().all()):
            raise InputValidationError(f'{area_path}: every interval must contain each expected element exactly once')
        values = frame[[category.id for category in categories]].to_numpy()
        if not np.isfinite(values).all() or (values < 0).any():
            raise InputValidationError(f'{area_path}: acreage must be finite and nonnegative')
        normalized_dates = frame['date'].map(dict(zip(endpoints, ends)))
        raw_values = frame.drop(columns=['date']).assign(interval_end=normalized_dates)
        raw_values = raw_values.set_index(['interval_end', 'element_id']).sort_index()
        # FACT converts file values to model square feet; one acre is 43,560 ft2.
        # Node conversion and the mesh-area denominator are separate conventions.
        acres = raw_values * (factor / 43560.0)
        return LandUseSource(
            source, categories, endpoints, ends, self.coverage_start, raw_values, acres,
            factor, 'acres' if factor == 43560 else 'square_feet', self.unit_basis,
            component_path, area_path, tuple(getattr(component, 'budget_crop_codes', ())),
        )