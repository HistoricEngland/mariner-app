# Mariner App

This is an Arches extension app providing the basis of a Marine Historic Environment inventory system using the Arches cultural heritage inventory software.

Mariner App was initially developed for use by Historic England to provide an inventory for maritime cultural heritage off the coast of England.  As such, there are elements of the out-of-the-box Mariner App which reflects this geographic focus.
## Requirements

- Python 3.10+ (Check the Arches python requirements and match your Python version))
- Arches ==7.6.22

## Heritage asset names with Arches Querysets

`mariner_app.heritage_asset_names.get_heritage_asset_names()` reproduces the
HAPI SQL query for the current Monument, Historic Aircraft, and Maritime Vessel
resource models:

```python
from mariner_app.heritage_asset_names import get_heritage_asset_names

rows = list(get_heritage_asset_names(resource_ids, language_code="en"))
# [{"resourceinstanceid": UUID(...), "heritageassetname": "Asset name 123"}]
```

The nine node UUIDs listed in the module match the current Mariner database.
Names have datatype `string`, name-use types have datatype `concept`, and primary
reference numbers have datatype `number`. Missing nodes raise
`ImproperlyConfigured`; a resource model with different node UUIDs needs its own
mapping. Graph slugs are
not required. The implementation uses AQS's `TileTree` and its
`get_node_value_expression()` utility (also used by AQS's alias generator) with
Django ORM aggregation and a correlated subquery. It assigns query-local aliases
to avoid collisions between node aliases in different graphs. It does not
execute raw SQL or load tile trees into Python. Reference numbers use JSON text
extraction rather than AQS's float conversion, preserving large integers,
numeric zero, and any legacy textual reference values exactly as PostgreSQL
`->>` returns them.
Node/datatype metadata is loaded when constructing the queryset; evaluating its
results uses one SQL query (zero for an empty ID list).

Exactly one primary-name **tile** is required, including tiles whose name is
null. Multiple candidates fall back to the graph name, even if their names are
identical. Only a final lowercase `s` is removed from fallback graph names.
Blank references are excluded, but reference whitespace and duplicate reference
tiles are preserved. Results are ordered by resource UUID, without a defined
order among references for the same resource. Like PostgreSQL `trim()`, trimming
removes spaces, not all whitespace.

Asset names are localized JSON of the form
`{"en": {"value": "Asset name", "direction": "ltr"}}`; graph names are JSON of
the form `{"en": "Monument"}`. Both implementations extract the same explicit
language, defaulting to English, without language fallback. A missing asset-name
translation falls back to the graph name in that language. A missing graph
translation propagates null unless a usable primary name exists.

The Primary value UUID is `2df285fa-9cf2-45e7-bc05-a67b7d7ddc2f`. It is matched
against **Name Use Type**, not **Name Type**, which is a separate node. Candidate
tiles still count when their name or requested translation is missing.

The comparison tests install the reference SQL function and create synthetic
graphs, nodes, resources, and tiles with the live model UUIDs, node datatypes,
and nodegroup cardinalities **only in Django's isolated test database**. They
compare both implementations' column names, UUID/text values, nulls, duplicate
rows, and UUID ordering. They do not install the function or add fixtures to the running
application database, and do not need Elasticsearch indexing.

Run them in the running Docker application container:

```bash
docker exec -w /web_root/mariner_app marinerapp \
  python manage.py test tests.test_heritage_asset_names \
  --settings=tests.heritage_asset_names_settings \
  --testrunner=django.test.runner.DiscoverRunner --debug-mode --noinput
```

The standard Django runner avoids Arches' Elasticsearch test-index setup.
`--debug-mode` matches the Arches runner's debug setting, allowing the isolated
dummy test cache. Outside Docker, use `--settings=tests.test_settings`.

Each comparison test run writes `tests/heritage_asset_names_report.md`, containing
three sections:

1. **Batch performance summary**: one row per comparison request, with test/batch
   name, language, distinct requested resource count, actual SQL/AQS result-row
   counts, whole-batch SQL/AQS timing statistics, and sample/warm-up counts.
2. **Resource-name comparisons**: request ID, test/batch name, language, resource
   UUID, SQL name, AQS name, and a final `true`/`false` match column, without timings.
3. **Raw measured samples**: all measured SQL/AQS timings, excluding warm-ups.

A unique request ID links all three sections, including repeated comparisons
within the same test. Requested resource counts exclude duplicate input UUIDs;
returned row counts may be lower (excluded references) or higher (duplicate
reference results). Override the report location
with the `HERITAGE_ASSET_NAMES_REPORT` environment variable in the test process.
The report is overwritten for each run and retained even when a name assertion
fails. It contains synthetic test data, not live data.

Durations cover the whole request and appear only once per comparison batch in
the summary; they are not per-resource timings. AQS timing includes metadata
loading and queryset evaluation; SQL timing includes execution and fetching.
Ordinary correctness tests run once, SQL first, with one timing sample; those
measurements are not a controlled benchmark.
Names are quoted and escaped to distinguish whitespace, empty strings, nulls,
and missing rows. Duplicate results are retained. Error-only tests are excluded.
To also validate the report formatter, include
`tests.test_heritage_asset_names_report` and
`tests.test_heritage_asset_names_benchmark` in the test command.

### Optional steady-state benchmark

Keep regular correctness tests fast. Opt into repeated timing with:

```bash
docker exec -e HERITAGE_ASSET_NAMES_BENCHMARK=1 \
  -w /web_root/mariner_app marinerapp \
  python manage.py test tests.test_heritage_asset_names \
  --settings=tests.heritage_asset_names_settings \
  --testrunner=django.test.runner.DiscoverRunner --debug-mode --noinput
```

For each comparison, this first performs the normal correctness/query-count
checks, then performs **3 warm-up pairs and 30 measured pairs** against unchanged
fixtures. The initial correctness measurement and warm-ups are excluded from
benchmark statistics. Every pair is validated against the expected result,
including UUID/text shape, duplicates, nulls, and ordering. Each AQS request
builds and evaluates a new queryset. SQL-first and AQS-first order alternates,
with 15 of each among the measured pairs.

The separate default output is `tests/heritage_asset_names_benchmark.md`, so a
benchmark does not overwrite the ordinary correctness report. The median is
the headline statistic; p95 (nearest rank), mean, minimum, maximum, and sample
counts show variability. All outliers are retained, and raw measured samples
are included after the table. There are no timing pass/fail thresholds.

These are steady-state measurements within one process, not cold-start results.
Synthetic fixtures mainly measure request/ORM overhead, not production-scale
database performance. Container contention, instrumentation, and database/OS
caches still affect measurements.

### Public-API-first AQS implementation

`mariner_app.heritage_asset_names_aqs.get_heritage_asset_names()` is a separate
alias-first alternative; the original UUID-based implementation remains intact.
It requires the graph slugs `monument`, `historic-aircraft`, and `maritime-vessel`,
with their configured nodegroup and node aliases. It selects tiles with public
`TileTree.get_tiles()` queries and reads node values by their alias-resolved
node IDs, rather than calling AQS's expression-generation utilities directly.
Inline `AQS:` and `Django:` comments identify the responsibilities of each layer.

```python
from mariner_app.heritage_asset_names_aqs import get_heritage_asset_names

rows = list(get_heritage_asset_names(resource_ids, language_code="en"))
```

Window functions partitioned by resource enforce exactly one candidate across all
supported name groups. A tile-ID semijoin preserves duplicate reference labels
despite AQS's `distinct()` behavior. Exact numeric identifier text intentionally bypasses
AQS's float conversion. Missing/misconfigured aliases or datatypes raise
`ImproperlyConfigured`; this version deliberately requires aliases, unlike the
UUID-based implementation. Output shape, ordering, nulls, language behavior,
cross-group candidates, and reference multiplicity are preserved. Construction
loads node metadata, while evaluating the results remains one SQL statement.

The public implementation combines all six AQS selections (names and references
for each graph) with Django's queryset OR operator into one tile-ID semijoin.
Requested resource IDs are applied once to that combined selection, not copied
into every branch, which also avoids scanning unrelated resources for small
batches. The selected name and reference tiles are then read in one pass:
window functions over each resource's tiles count the primary-name candidates
and pick the name, and a filter on a window-derived marker keeps only reference
tiles with a nonblank reference. Django applies that filter in an outer
`QUALIFY`-style query, after the windows, so the name tiles are counted before
they are discarded. Earlier versions used correlated subqueries, which
PostgreSQL ran once per reference row, rescanning each resource's tiles; the
set-based form is about 5.7× faster on all live resources (see
`tests/heritage_asset_names_live_aqs_comparison_window.md`).
The tiles of all three graphs share one queryset, so values are read by
alias-resolved node ID; AQS's per-graph value aliases would collide there.
`Coalesce` evaluates the primary-name expression once, rather than repeating it
in a null check and result branch. The full candidate count is intentional:
stopping after two candidates could hide malformed name-use UUIDs that the SQL
would reject. These changes require no new dependencies, database indexes, or
live writes.

Run `tests.test_heritage_asset_names_aqs` with the same test settings/runner as
above. It reuses the SQL-oracle scenarios and also compares normal results with
the original implementation. It adds coverage for configuration errors,
duplicate tiles, and changing node UUIDs while retaining their aliases. Its
separate reports use the `heritage_asset_names_public_aqs` filename prefix.
`HERITAGE_ASSET_NAMES_BENCHMARK=1` enables the same 3/30 repeated test benchmark.

### Read-only live-data and fresh-process benchmarks

Run the separate opt-in CLI, **not the Django test runner**, for live-data batches:

```bash
docker exec -w /web_root/mariner_app marinerapp \
  python -m tests.benchmark_heritage_asset_names \
  --batch-sizes 1 10 100 --warmups 3 --repetitions 30
```

By default, resources are sampled deterministically from the three resource
models and interleaved. Supply `--ids UUID UUID ...` to choose representative
resources explicitly; each requested batch size must fit the supplied/sample
IDs. Use `--language cy` to select a language and `--output PATH` to change the
default `tests/heritage_asset_names_live_benchmark.md` report. The report contains
actual resource names and should be treated as application data.

Select the new implementation with `--aqs-implementation public` (the default
is `original`). The selection also propagates to fresh-process workers:

```bash
docker exec -w /web_root/mariner_app marinerapp \
  python -m tests.benchmark_heritage_asset_names \
  --aqs-implementation public --batch-sizes 1 10 1000 \
  --warmups 3 --repetitions 30
```

Its default live report is `tests/heritage_asset_names_live_public_aqs_benchmark.md`.
For the complete population, combine `--aqs-implementation public` with
`--all-resources` and an explicit output path to preserve the sampled report.
The public-AQS live reports from 27 October predate both optimisations. The report
`tests/heritage_asset_names_live_public_aqs_optimised_all_resources.md` records
the first (combined-selection) optimisation alongside a same-snapshot comparison
with its pre-optimisation version. The window-function version is recorded in
`tests/heritage_asset_names_live_aqs_comparison_window.md`.

To compare the **UUID-based and alias-based AQS implementations head to head**,
use `--aqs-implementation both`. Each repetition runs SQL, `heritage_asset_names`
and `heritage_asset_names_aqs` in the same read-only snapshot, cycling through
all six request orders (use a multiple of 6 repetitions to balance them):

```bash
docker exec -w /web_root/mariner_app marinerapp \
  python -m tests.benchmark_heritage_asset_names \
  --aqs-implementation both --all-resources --warmups 3 --repetitions 30
```

Its default report is `tests/heritage_asset_names_live_aqs_comparison.md`. It
contains timings only, with no resource names or UUIDs: a UUID-vs-alias table
(median ratio, median paired per-repetition difference, and how often alias
won), per-implementation statistics relative to SQL, and raw samples. All
results must still match the initial SQL result. `--cold-start-runs` runs each
implementation in its own fresh process.

To benchmark **all eligible live resources as one complete batch**, rather than
a sample, use:

```bash
docker exec -w /web_root/mariner_app marinerapp \
  python -m tests.benchmark_heritage_asset_names \
  --all-resources --warmups 3 --repetitions 30 \
  --output tests/heritage_asset_names_live_all_resources.md
```

Eligible means a resource belongs to one of the three supported models and has
at least one supported primary-reference tile with a non-null, non-space-only
reference value. Zero is a valid reference. A primary name is not required:
the normal graph-name fallback still applies. Requested resource IDs are
deduplicated, while duplicate reference results are preserved. Eligibility is
selected inside the same read-only snapshot as the warm benchmark and is not
included in request timings. An empty eligible set fails explicitly.

`--all-resources` cannot be combined with `--ids` or `--batch-sizes`. Repetitions,
warm-ups, language, and optional fresh-process measurements remain configurable.
The complete population can take substantially longer than sampled batches and
produces a correspondingly larger report. The output override above preserves
the default sampled report.

All warm requests share a **read-only, repeatable-read transaction**, with a
60-second statement timeout. The CLI executes only the reference SQL function's
SELECT body; it never creates schemas, installs/replaces functions, or creates
fixtures in the live database. Every repetition must match the initial SQL
result, preserving duplicates and output shape.

Add `--cold-start-runs 10` to measure first requests in fresh Python processes,
reported separately as `live-fresh-process-batch-N`, without warm-ups. Each SQL
or AQS request gets its own fresh process and read-only transaction; order
alternates between repetitions. These timings **exclude process startup,
Django initialization, and connection setup** but include first-request query
construction/metadata loading. They do not flush database/OS/shared caches.
Independent worker snapshots may observe concurrent live changes; mismatches
fail explicitly rather than being ignored. Worker failures/timeouts also fail
the benchmark. No live performance thresholds are asserted.

## Installing for Development

For development purposes, you can treat this app as a standard Arches project. Either use the instructions for developing an Arches project or use the arches-containers configuration included in this repository.

- **For development (standard):**

  ```bash
  pip install -e '/path/to/mariner_app[dev]'
  ```

  This installs the app in editable mode, allowing you to make changes and see them reflected immediately without needing to reinstall.

- **For development using included arches-container configuration:**

  > arches-containers is a tool developed by Historic England to enable the quick set up of development environments.  You can find out more about arches-containers and how to install and use it, please see the [arches-containers documentation on PyPI](https://pypi.org/project/arches-containers/)
  
  Please ensure that you clone the mariner-app repository into a directory that uses underscores instead of hyphens, as the arches-containers configuration expects this format. For example, clone it to `mariner_app`.

    ```bash
    git clone https://github.com/HistoricEngland/mariner-app.git mariner_app
    ```

  This repository includes an `arches-containers` project configuration, so you can import, activate, and start the system as follows:

  1. Ensure Docker is installed and running.
  2. Navigate to your workspace directory (the root where your projects and containers live).
  3. Import the arches-container project configuration:

     ```bash
     act import -p mariner_app
     ```

  4. Activate the project:

     ```bash
     act activate -p mariner_app
     ```

  5. Start the system:

     ```bash
     act up
     ```

  6. Once setup and webpack builds are complete, open a browser and navigate to `http://localhost:8002` or use `act view` in a terminal to open the project in your default browser.



## Using This App in Your Arches Project

While you can use Mariner App as a standalone app, you may wish to use it as part of an Arches Project.  The following steps explain how to do this:

### 1. Add to `your_project/pyproject.toml`

  Add the following to your `pyproject.toml` dependencies (in the `[project]` section):

  ```toml
  mariner-app @ git+https://github.com/HistoricEngland/mariner-app.git@release/1.0.1
  ```
 
  Example:

  ```toml
  dependencies = [
      "arches==7.6.22",
      "mariner-app @ git+https://github.com/HistoricEngland/mariner-app.git@release/1.0.1",
  ]
  ```

  ### 2. Update `your_project/your_project/settings.py`

Add the following to the appropriate locations:

```python
DATATYPE_LOCATIONS.append("mariner_app.datatypes")
FUNCTION_LOCATIONS.append("mariner_app.functions")
SEARCH_COMPONENT_LOCATIONS.append("mariner_app.search.components")
```

Add to `INSTALLED_APPS` and `ARCHES_APPLICATIONS`:

```python
INSTALLED_APPS = (
    ...
    "your_project",
    "mariner_app",
)
ARCHES_APPLICATIONS = ("mariner_app",)
```

### 3. Update `your_project/your_project/urls.py`

Include the app's URLs (add project URLs before this):

```python
urlpatterns = [
    # ... your project urls ...
    path("", include("mariner_app.urls")),
]
```

### 4. Run Database Migrations

Run the following command to apply any database migrations required by this app:

> if using the arches-containers, run this in the application container

```bash
python manage.py migrate
```

## 5. Install and Build Front-End Dependencies

From the directory containing your `your_project/package.json`:

> if using arches-containers, run this in the application container or restart the webpack

```bash
npm install
npm run build_development
```

## 6. Start Your Arches Project

```bash
python manage.py runserver
```

## Loading the Mariner App Package

To complete setting up Mariner App, you will need to load the package which contains the graphs, reference data and settings.

Run the following command to load the package files for Mariner App:

> if using the arches-containers, run this in the application container

```bash
python manage.py packages -o load_package -a mariner_app
```

## License

This project is licensed under the GNU AGPLv3. See the LICENSE file for details.

---


For more information on deploying your Arches project, see the [Arches Deployment Guide](https://arches.readthedocs.io/en/stable/deployment/).
