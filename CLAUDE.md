# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Mariner App is an **Arches 7.6 application** (Django 4.2, Python 3.10–3.12) built by Historic England as a marine historic-environment inventory. It works on its own as an Arches project (`mariner_app/settings.py`, `manage.py`). Another Arches project can also install it as a package through `ARCHES_APPLICATIONS`. Pinned versions: Arches `stable/7.6.22` (npm) and `arches>=7.6.17,<8` (pip). It depends on `arches-he-descriptors` and `arches_querysets[drf]`, and both of their URL sets are included in `mariner_app/urls.py`.

The parent workspace (`../`) has a checkout of Arches core (`../arches`) and the arches-containers config (`../.arches_containers`). Treat Arches core as a read-only reference.

## Dev environment (arches-containers / Docker)

Development normally uses the arches-containers (`act`) Docker setup in `.ac_mariner_app/`. The app container is called **`marinerapp`**, and the code is mounted at `/web_root/mariner_app`. The site runs at `http://localhost:8002`. The stack also has `marinerapp-webpack`, `marinerapp-livereload`, `db-marinerapp` (PostGIS), `elasticsearch-marinerapp` and `rabbitmq-marinerapp`. Run Django commands inside the container:

```bash
docker exec -w /web_root/mariner_app marinerapp python manage.py <command>
```

**Rule: run all Python and CLI work inside the `marinerapp` container.** Python is installed only inside the running Docker container, not on the host. Before running any Python code (scripts, `manage.py`, tests, benchmarks, `pip`, `python -c`), check that the containers are running:

```bash
docker ps --format '{{.Names}}\t{{.Status}}'
```

If `marinerapp` (or a dependency it needs, such as `db-marinerapp` or `elasticsearch-marinerapp`) isn't up, stop and ask the user to start the containers. Don't start them yourself, and don't fall back to running Python on the host. Commands written as bare `python ...` elsewhere in this file must be run through `docker exec -w /web_root/mariner_app marinerapp ...`.

`mariner_app/settings_local.py` and `tests/test_settings_for_docker.py` are gitignored, local-only files. The `.py.template` is the starting point for the Docker test settings.

## Commands

Python:
```bash
# Full test suite (what CI runs; uses Arches' ArchesTestRunner, needs Postgres + Elasticsearch)
python manage.py test tests --settings=tests.test_settings
# Single module / test
python manage.py test tests.test_bng_datatype --settings=tests.test_settings
python manage.py test tests.test_bng_datatype.BNGCentreDataTypeTests.test_bngcentrepoint_validation --settings=tests.test_settings
# CI also checks for missing migrations
python manage.py makemigrations --check
# Load graphs, concepts and system settings from mariner_app/pkg
python manage.py packages -o load_package -a mariner_app
```

Run the heritage-asset-names tests in Docker against the running app's DB/ES settings, using Django's standard runner. This skips Arches' Elasticsearch test-index setup:
```bash
docker exec -w /web_root/mariner_app marinerapp python manage.py test tests.test_heritage_asset_names \
  --settings=tests.heritage_asset_names_settings \
  --testrunner=django.test.runner.DiscoverRunner --debug-mode --noinput
```
Set `HERITAGE_ASSET_NAMES_BENCHMARK=1` to also run the 3-warm-up / 30-sample benchmark. Live-data benchmarks are a separate read-only CLI, not tests: `python -m tests.benchmark_heritage_asset_names` (see the README for flags).

Frontend (run from the repo root):
```bash
npm run build_development   # eslint + vue-tsc + webpack dev build
npm run build_test          # what CI builds
npm run vitest              # frontend unit tests with coverage
npm run eslint:check | npm run prettier:check | npm run ts:check
```

Formatting: Python uses **black 24.4.2**, and the frontend (`mariner_app/src`) uses prettier + eslint. All of these run as pre-commit hooks (`.pre-commit-config.yaml`).

## Architecture

Arches finds the custom code through the `*_LOCATIONS` lists in `settings.py`. Each Python extension has a **frontend half** under `mariner_app/media/js/views/components/...`, which its `details` dict refers to by component path. Each also has a **DB registration**:

- `datatypes/bngcentrepoint.py`: the `bngcentrepoint` datatype (British National Grid alphanumeric reference with validation). Its default widget is `bngpoint` (`media/js/views/components/widgets/bngpoint.js`).
- `functions/bngpoint_to_geojson_function.py` and `functions/geojson_to_bngpoint_function.py`: node functions that keep a BNG node and a GeoJSON node in sync when tiles are saved.
- `migrations/84748_initial_bng_photo_datatype_functions_widgets.py` registers the functions, widget and datatype with fixed UUIDs. If you change an extension's `details` (e.g. `functionid`), the migration must stay consistent with it.

Package data in `mariner_app/pkg/` is the domain model. It holds resource models (`graphs/resource_models/*.json`: Monument, Maritime Vessel, Historic Aircraft, Wreck Site, ...), branches, SKOS concept XML (`reference_data/concepts/`, mostly the FISH thesauri), CIDOC-CRM ontologies and system settings. The node, nodegroup and value UUIDs that the Python code hard-codes come from these graph files.

**Heritage asset names** are two ORM ports of the HAPI SQL query. They return `{resourceinstanceid, heritageassetname}` for Monument, Historic Aircraft and Maritime Vessel resources:
- `heritage_asset_names.py`: keyed by **node UUIDs**. It uses AQS `TileTree` plus `get_node_value_expression()`.
- `heritage_asset_names_aqs.py`: keyed by **graph slugs and node aliases**, using only public `TileTree.get_tiles()` APIs.

Both must stay behaviourally identical to the reference SQL function (`tests/fixtures/heritage_asset_names.sql`). That covers null handling, duplicate references, ordering, the language extraction with no fallback, the graph-name fallback when there isn't exactly one primary-name tile, and preserving exact numeric reference text instead of AQS float conversion. Each must evaluate in a single SQL query. The tests compare both implementations against the SQL oracle in an isolated test DB. The README section on this documents the exact semantics, so read it before changing either module. Generated `tests/**/*.md` reports are gitignored.

## Notes

- The README's install snippet says `mariner_app.search.components`. The actual setting is `SEARCH_COMPONENT_LOCATIONS.append("mariner_app.search_components")`.
- CI (`.github/workflows/main.yml`) builds both the PR's target branch and its feature branch on Python 3.10–3.12 against `postgis/postgis:13-3.0`, then compares coverage.
