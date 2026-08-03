# Evidence recipes — how to inspect a dataset without reading it

Per-format sampling recipes for Stage 1. The output of these commands *is* your
evidence: file counts, column names, dtypes, shapes, and units are facts you may state
directly in the manifest.

## The big-data rule

**Sample, never load.** Datasets here run to hundreds of GB. Read headers, first rows,
tree structures, and metadata — never a whole file. Concretely:

- Never `cat` a data file. Use `head`, `wc -l`, or a library that reads lazily.
- Cap `pandas.read_csv` with `nrows=`; never call it bare on an unknown file.
- Check size before touching anything: `du -h <file>`.
- **Nothing is uploaded during drafting.** Upload happens only at Stage 6, after Gate #2.

## Start here — the tree

```bash
du -sh .                                                     # total size
find . -type f -not -path '*/.*' | wc -l                     # file count
find . -type f -not -path '*/.*' | sed 's/.*\.//' | sort | uniq -c | sort -rn | head -20
find . -maxdepth 2 -type d -not -path '*/.*'                 # layout
find . -type f -not -path '*/.*' -exec du -h {} + | sort -rh | head -10   # biggest files
```

**Directory names are metadata.** `data/{train,test}/` implies ML splits.
`samples/Fe2O3/`, `samples/TiO2/` implies a material axis. `images/{cat,dog}/` implies
classification labels. Say so in the description and, if ML-shaped, in `ml.splits` /
`ml.keys`.

## Prose first

```bash
ls -la; find . -maxdepth 3 -iname 'README*' -o -iname 'LICENSE*' -o -iname 'CITATION*' \
  -o -iname '*.bib' -o -iname '*.tex' -o -iname 'paper*' -o -iname '*.pdf'
```

Which section feeds which field:

| Source | Feeds |
| --- | --- |
| README title / H1 | `title` |
| README intro, paper abstract | `description` paragraph 1 |
| README "Data"/"Contents"/"Files" section | `description` paragraph 2, `ml.keys` |
| Paper methods section | `methods`, `facility` |
| Paper acknowledgements | `funding` |
| Paper author list + affiliations | `authors` (verify against Crossref) |
| `LICENSE` / `COPYING` | `license` (match text to an SPDX id) |
| `CITATION.cff`, `*.bib`, README badges | `dataset_doi`, `related_dois` |
| Column headers, units in headers | `ml.keys` (`name`, `units`, `dtype`) |

Verify every DOI you find: `https://api.crossref.org/works/{doi}` via WebFetch. Match the
returned title against the paper before using its authors or year.

## CSV / TSV

```bash
head -3 data.csv
wc -l data.csv
```

With pandas available (an optional extra: `pip install 'mdf-cli[extractors]'`):

```python
import pandas as pd
df = pd.read_csv("data.csv", nrows=1000)
print(list(df.columns)); print(df.dtypes); print(df.head(3))
```

Row count without loading: `wc -l` minus the header. Record column names, dtypes, and
row count. Units are often embedded in headers — `energy_eV`, `T (K)` — pull them into
`ml.keys[].units`. Many similar CSVs: sample one, confirm the rest share the header with
`head -1`, and report the count.

The CLI can do this for you and write it into `mdf.yaml`'s `auto_metadata`:

```bash
mdf manifest discover "*.csv" --preview      # --preview prints without writing
```

## Excel

```python
import openpyxl
wb = openpyxl.load_workbook("f.xlsx", read_only=True)
print(wb.sheetnames)
ws = wb[wb.sheetnames[0]]
print(ws.max_row, ws.max_column)
for row in ws.iter_rows(max_row=3, values_only=True):
    print(row)
```

Sheet names are often the sample/condition axis — worth reporting in the description.

## HDF5 / NetCDF

```python
import h5py
def walk(name, obj):
    if isinstance(obj, h5py.Dataset):
        print(name, obj.shape, obj.dtype, dict(obj.attrs))
with h5py.File("f.h5", "r") as f:
    print(dict(f.attrs))
    f.visititems(walk)
```

Attributes frequently carry units, instrument names, and acquisition dates — exactly the
evidence `methods` and `facility` need. Never read dataset *values*; shapes and dtypes
are enough. CLI alternative: `h5ls -r f.h5`. NetCDF: `ncdump -h f.nc`.

## JSON / JSON Lines / YAML

```bash
head -c 2000 f.json    # the record schema is visible in the first 2 KB
head -1 f.jsonl        # one record from a line-delimited file
```

Report the top-level keys and the shape of one representative record. Only if the file
is small (check with `ls -lh` first — say, under a few MB) is a full parse acceptable:
`python -c "import json;d=json.load(open('f.json'));print(type(d), list(d)[:20] if isinstance(d,dict) else len(d))"`.

## Images

```bash
find . -iname '*.png' -o -iname '*.jpg' -o -iname '*.tif' | wc -l
find . -maxdepth 2 -type d          # directory names are often class labels
python -c "from PIL import Image; im=Image.open('s.png'); print(im.size, im.mode, im.format)"
```

Record: count, formats, dimensions of a sample (note if they vary), and the directory
structure. If images sit in per-class subdirectories, that is a classification dataset —
say so, and set `ml.keys` with a `target` key whose `classes` are the directory names.

## Archives — list, don't extract

```bash
unzip -l a.zip | head -30
tar -tzf a.tar.gz | head -30
tar -tzf a.tar.gz | wc -l
```

Never fully extract a large archive to inspect it. If the contents are opaque, extract a
single member to a temp dir and sample that.

## Materials / simulation formats

| Format | How to read it | What it gives you |
| --- | --- | --- |
| `OUTCAR`, `INCAR`, `POSCAR`, `vasprun.xml` | `head -50 INCAR`; `grep -m1 'vasp\.' OUTCAR`; `grep 'ENCUT\|ISMEAR\|EDIFF' INCAR` | VASP version, functional, cutoff, k-points → `methods` |
| `*.cif` | `grep '_chemical_formula\|_symmetry_space_group\|_cell_length' f.cif` | Composition, space group → `subjects`, `description` |
| `*.xyz`, `*.extxyz` | `head -2 f.xyz` (atom count + comment line, which often carries energies/lattice) | System size, properties present |
| LAMMPS `log.*`, `*.data` | `head -40` | Force field, ensemble, timestep → `methods` |
| Quantum ESPRESSO `*.in` / `*.out` | `grep 'pseudo\|ecutwfc\|K_POINTS'` | Pseudopotentials, cutoffs → `methods` |
| `*.xrdml`, `*.raw`, `*.spc`, `*.dm3`, `*.emd` | `strings f | head -40` | Instrument model, operator, acquisition date → `facility`, `methods` |
| NeXus `*.nxs` | h5py recipe above | Beamline, instrument, sample → `facility` |

For any unfamiliar binary format: `file f.dat`, then `strings f.dat | head -50`. Vendor
headers usually name the instrument in the first few hundred bytes. If you cannot
identify it, say so and ask the user — do not guess the instrument.

## Turning evidence into the gate

Keep a running list as you go, because Gate #1 requires you to show
**field → value → evidence** for every field you filled. A claim you cannot attribute to
one of these recipes, a document you read, or the user does not belong in the manifest.
