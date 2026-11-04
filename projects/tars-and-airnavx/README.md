# TARS and AirnavX local AI source

Saved Pythonista TARS client, local manual index/search service, and Open WebUI tool integrations.

`tars_pythonista.py` is the iPhone/Pythonista client. Configure LLM_URL, MODEL, and optional bridge URL before running it. The default endpoints in this source snapshot use loopback placeholders; a phone needs the actual reachable address of your own service. Personal profile details were removed.

The AirnavX indexer and search server work with separately supplied, authorized local manual data. No manuals, extracted documents, indexes, account sessions, or browser data are included. Check each script for its CLI options and dependency imports. These are saved source snapshots, not a fresh export of the running Proxmox guest. Maintenance responses require verification against current official documentation.
