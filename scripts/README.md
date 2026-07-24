# Repository scripts

Scripts in this directory must work on both Windows and macOS. Prefer Node.js or Python over platform-specific shell scripts.

## sb6657 style corpus

Fetch the public read-only barrage corpus into ignored local data, then derive an aggregate-only profile:

```powershell
python scripts/fetch_sb6657_corpus.py --page-size 500 --delay 0.35
python scripts/profile_sb6657_corpus.py
```

The fetcher deliberately omits `dpahjdoiaw` and `siteToken`. Never commit `.advx-data/sb6657/corpus.jsonl`; only the reviewed aggregate profile belongs in source control.
