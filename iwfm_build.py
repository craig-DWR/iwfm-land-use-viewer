"""Opt-in adapter ingestion for the existing C2VSimFG snapshot builders."""

import json
import struct
from datetime import datetime
from pathlib import Path


def add_build_options(parser):
    parser.add_argument('--reader', choices=('legacy', 'iwfm-io'), default='legacy')
    parser.add_argument('--rootzone-main', help='Required with --reader iwfm-io')
    parser.add_argument('--preprocessor-main', help='Required with --reader iwfm-io')
    parser.add_argument('--simulation-main', help='Read coverage start from simulation BDT')
    parser.add_argument('--coverage-start', help='Explicit inclusive ISO date/datetime or IWFM timestamp')
    parser.add_argument('--model-config', help='Presentation JSON; requires --reader iwfm-io')


def store_source(connection, source, expected_aliases, min_date, *, category_ids=False):
    """Bridge normalized acres to float32 vectors and the existing interval-end keys."""
    from iwfm_input_adapter import InputValidationError

    columns = {(category.id if category_ids else category.legacy_alias): category.id for category in source.categories}
    if (None in columns or len(columns) != len(source.categories)
            or set(columns) != set(expected_aliases)):
        raise InputValidationError(
            f'{source.component_path}: categories do not match C2VSimFG aliases; '
            f'expected {expected_aliases}, found {list(columns)}')
    dates = [endpoint.date().isoformat() for endpoint in source.interval_ends]
    if any(endpoint.time() != datetime.min.time() for endpoint in source.interval_ends):
        raise InputValidationError(f'{source.area_path}: viewer requires midnight interval endpoints')
    selected = source.acres[[columns[alias] for alias in expected_aliases]]
    records = []
    inserted = 0
    for row in selected.itertuples(name=None):
        (endpoint, element), *values = row
        effective = endpoint.date().isoformat()
        if effective < min_date:
            continue
        records.append((source.source, effective, int(element), struct.pack('<' + 'f' * len(values), *values)))
        if len(records) >= 10000:
            connection.executemany('INSERT INTO areas VALUES (?,?,?,?)', records)
            inserted += len(records)
            records.clear()
    if records:
        connection.executemany('INSERT INTO areas VALUES (?,?,?,?)', records)
        inserted += len(records)
    print(f'{source.source}: {len(dates)} update dates, {inserted:,} adapter records retained', flush=True)
    return dates


class AdapterBuild:
    def __init__(self, args):
        try:
            from iwfm_io import parse_iwfm_date, strict_mode
            from iwfm_io.readers import preprocessor
            from iwfm_io.readers.simulation import read_simulation_main
            from iwfm_input_adapter import C2VSIMFG_ALIASES, IWFMInputAdapter, InputValidationError, UnitBasis
        except ModuleNotFoundError as error:
            raise ValueError('--reader iwfm-io requires dependencies from requirements-adapter.txt; '
                             'install them in the selected environment') from error

        if not args.rootzone_main or not args.preprocessor_main:
            raise InputValidationError('--reader iwfm-io requires --rootzone-main and --preprocessor-main')
        root = Path(args.rootzone_main).resolve()
        pp_path = Path(args.preprocessor_main).resolve()
        if args.coverage_start:
            start = (parse_iwfm_date(args.coverage_start) if '_' in args.coverage_start
                     else datetime.fromisoformat(args.coverage_start))
            evidence = 'explicit --coverage-start'
        elif args.simulation_main:
            # BDT is authoritative; the first area endpoint only closes a block.
            with strict_mode(True):
                simulation = read_simulation_main(args.simulation_main)
            start = parse_iwfm_date(simulation.sim_begin)
            simulation_root = Path(simulation.file_paths.get('rootzone_main') or '').resolve()
            if simulation_root != root:
                raise InputValidationError('Simulation main must reference the supplied --rootzone-main')
            evidence = f'{Path(args.simulation_main).name}: BDT={simulation.sim_begin}'
        else:
            raise InputValidationError('Supply --simulation-main or --coverage-start; interval ends do not define coverage start')
        if start > datetime(args.start_year, 1, 1):
            raise InputValidationError('Coverage start is after the viewer chart start; choose a later --start-year')
        with strict_mode(True):
            pp = preprocessor.read_preprocessor_main(pp_path, follow_references=False)
            element_path = Path(pp.file_paths['element'])
            elements = preprocessor.read_elements(element_path)
        self.adapter = IWFMInputAdapter(
            root, working_directory=Path(args.simulation_main).resolve().parent if args.simulation_main else root.parent.parent,
            element_ids=set(map(int, elements.data['element_id'])),
            unit_basis=UnitBasis.from_preprocessor(pp_path), coverage_start=start,
            legacy_aliases=C2VSIMFG_ALIASES,
        )
        self.config = None
        if getattr(args, 'model_config', None):
            self.config = json.loads(Path(args.model_config).read_text(encoding='utf-8'))
        self.codes = {}
        self.categories = []
        if self.config is None and set(self.adapter.sources) != {'nonponded', 'ponded', 'native', 'urban'}:
            raise InputValidationError('C2VSimFG viewer requires all four land-use components')
        self.paths = {'nodes': Path(pp.file_paths['node']), 'elements': element_path,
                      'rootzone_main': root, 'preprocessor_main': pp_path}
        if args.simulation_main:
            self.paths['simulation_main'] = Path(args.simulation_main)
        self.metadata = {'name': 'iwfm-io', 'version': '2.15.2',
                         'coverage_start': start.isoformat(), 'coverage_start_evidence': evidence,
                         'unit_basis': self.adapter.unit_basis.model_area_unit, 'sources': {}}

    def parse_area(self, connection, name, min_date, valid_ids, aliases):
        if valid_ids != self.adapter.element_ids:
            raise ValueError('Adapter and legacy geometry element IDs disagree')
        source = self.adapter.read_source(name)
        self.paths[name] = source.area_path
        self.paths[name + '_main'] = source.component_path
        self.metadata['sources'][name] = {
            'input_factor': source.input_factor, 'input_unit': source.input_unit,
            'original_endpoints': list(source.endpoints),
        }
        if self.config is not None:
            aliases = [category.id for category in source.categories]
            self.codes[name] = aliases
            self.categories.extend(source.categories)
        return store_source(connection, source, aliases, min_date, category_ids=self.config is not None)

    def presentation(self):
        from model_configuration import resolve_configuration

        return resolve_configuration(self.config, self.categories)