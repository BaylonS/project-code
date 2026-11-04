# Project code

Private source collection for local AI, homelab utilities, and personal projects. The `projects/` directory contains editable source snapshots organized by project.

| Project | Contents |
| --- | --- |
| tars-and-airnavx | Pythonista TARS client, local search/index tools, Open WebUI integrations |
| minddrop | Self-hosted capture inbox, optional local LLM integration, Docker setup |
| server-dashboard | Homelab dashboard and exporter tools |
| skywatch | Aircraft monitoring application |
| ksp-gps-tools | Kerbal Space Program GPS constellation scripts |
| icarus-mount-editor | Existing upstream editor checkout and local additions; original license retained |
| icarus-tools | Additional local Icarus analysis and editing utilities |
| las-mx-selfhost | Maintenance-reference application source; reference content excluded |
| aerolog-offline | Offline maintenance logbook application |
| las-turnover-offline | Turnover application code with embedded operational workbook data removed |

The separately published LLM controller is at [capital-rift-local-ai](https://github.com/BaylonS/capital-rift-local-ai).

## Snapshot scope

These files were collected from saved local project folders on November 3, 2026. They have not been synchronized against the running Proxmox machines. Credentials, browser profiles, databases, logs, exported records, aircraft manuals, extracted reference content, generated build directories, and model weights are excluded. Private LAN/Tailscale endpoints were replaced with placeholders; configure your own endpoints before running the code.

LAS Turnover's embedded workbook template and original XLSX content were removed. That source snapshot is intentionally incomplete until supplied with a clean template. LAS MX similarly needs separately supplied reference content. See their SOURCE-NOTES.md files.

Existing README files describe the original projects and may refer to files or deployment paths not included here. These are source snapshots, not a claim that every application has been rebuilt or tested. Python syntax is checked during packaging; live integrations and hardware behavior are not exercised.

## Editing and collaboration

Edit files under `projects/`, create a branch, and open a pull request. Keep this repository private when inviting collaborators to work-related code. Include reproduction steps and remove private records from screenshots or issue reports. Existing third-party licenses and attribution remain in their project directories. No new license is assigned to other code in this collection.

## Import mechanism

`source-bundle.zip` and `SOURCE_MANIFEST.json` retain the reviewed snapshot and per-file hashes. `import_sources.py` verifies the archive and file hashes, rejects unexpected paths, and expands it into `projects/`. The one-time GitHub Actions workflow performs this import and commits the resulting editable files. It does not execute any imported project code or deploy services.
