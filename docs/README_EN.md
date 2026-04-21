# Windows -> Ubuntu XRDP Offline Install Tool

中文版本: [README.md](../README.md)

## Project Overview

This is an Ubuntu offline installation tool (Tkinter GUI) designed for Windows-side operations.
It connects to a remote Ubuntu host over SSH, resolves direct .deb download links for target packages and dependencies, downloads them on Windows, then uploads them back to Ubuntu and runs an offline installation script.

The current repository is mainly optimized for XRDP offline deployment and repair scenarios, especially for these frequent issues:

- Installation interruptions caused by complex dependency chains
- Broken links after mirror-side 404 errors
- ABI/dependency incompatibilities caused by cross-release package mixing
- Reuse of already downloaded files and resumable workflows

## Typical Use Cases

- The remote Ubuntu server cannot install software directly from the internet
- You need to prepare offline packages centrally on Windows
- You need to repeatedly deploy XRDP and related components

## Key Features

- Automatically detects remote Ubuntu codename and architecture via SSH
- Uses apt-get --print-uris to get direct URLs for target packages and dependencies
- Automatically downloads .deb files and skips existing files
- Automatically retries failed downloads, and refreshes mirror directory links as a fallback for 404s
- Strictly filters cross-release packages to reduce mixed-install risks
- Automatically generates install_*.sh offline installation scripts
- Uploads offline bundles via SCP and executes installation remotely
- Supports step-by-step execution and one-click full workflow
- Supports resume capability (reuses downloaded .deb files and local scripts)

## Directory Guide

- release/ubantu_OfflineInstall_Tool_release.py: Main GUI program for release use (recommended entry)
- release/xrdp_offline_config_release.json: Release configuration file

## Runtime Environment

- Windows (Python 3.6+ installed)
- Available ssh/scp commands (OpenSSH client is recommended)
- Remote Ubuntu host accessible via an SSH alias

## Configuration

The release version reads release/xrdp_offline_config_release.json by default:

- SSH_ALIAS: Host alias in your SSH config
- BUNDLE_DIR_PREFIX: Local offline bundle directory prefix (the final directory name includes a target identifier)

Example:

{
  "SSH_ALIAS": "test",
  "BUNDLE_DIR_PREFIX": "offline_bundle"
}

Recommended pre-checks in terminal:

- ssh <SSH_ALIAS>
- scp any test file to the remote host

## Usage

### 1. Launch the GUI

Run from the repository root:

```
python release/ubantu_OfflineInstall_Tool_release.py
```

### 2. Set parameters in the GUI

- SSH alias
- Offline bundle directory prefix
- Package names to install (space- or comma-separated)
- Whether to include recommended packages

### 3. Execute steps in order (recommended)

- Step 1: Connect and read system information
- Step 2: Resolve direct URLs and package list
- Step 3: Download .deb files
- Step 4: Generate installation script
- Step 5: Upload to Ubuntu
- Step 6: Run installation remotely (you will be prompted for sudo password)

You can also use the one-click full workflow.

### 4. Outputs and Results

After execution, the following artifacts are generated/updated in the repository:

- ubuntu_version_*.txt: Remote OS version and architecture info
- package_list_*.txt: Resolved dependency package filename list
- download_links_*.txt: Direct dependency URLs
- offline_bundle_*/: Downloaded .deb files and install_*.sh

## FAQ

- Step 2 fails: First check SSH connectivity, remote apt source availability, and package name correctness.
- Step 3 shows 404: The tool will try to refresh links automatically. If it still fails, switch to an available mirror and retry.
- Remote install fails: Check sudo password, disk space, and dpkg state. If needed, run dpkg --configure -a to repair.
- XRDP blue screen: Install the core fix set first. After the core chain is stable, then install XFCE-related enhancement packages.

## Open Source License

This project is licensed under Apache License 2.0 (Apache-2.0).

- Full license text is in the repository root LICENSE file
- Project notice information is in the repository root NOTICE file

When using this project's code, you should generally follow these rules:

1. Keep copyright notices and license text
2. Add modification notices to files you changed
3. Include LICENSE and NOTICE in redistributions
4. Do not use author/contributor names for endorsement unless written permission is granted

Note: Apache-2.0 allows commercial use and closed-source integration, and provides explicit patent grant terms.

## Acknowledgements

Thanks to Ubuntu, OpenSSH, XRDP, and the related open-source communities.
