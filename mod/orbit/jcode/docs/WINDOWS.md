# Windows Support

Jcode supports Windows as a first-class platform. The Windows implementation uses native named pipes, Windows process management, PowerShell installation, and platform-specific launch-hotkey integration.

## Support status

| Area | Status |
|---|---|
| Windows 11 x64 | Supported and manually verified |
| Windows 11 ARM64 | Release builds and automated install checks |
| PowerShell installer | Tested on Windows CI |
| Native IPC and process lifecycle | Covered by targeted and end-to-end Windows tests |
| `jcode update` | Supported with SHA-256 verification |
| Release assets | x64 and ARM64 `.exe` and `.tar.gz` assets |
| Authenticode signing | Release pipeline ready; requires the one-time Azure configuration below |

PowerShell 5.1 or later is required by the installer. The x64 build is the default for Intel and AMD Windows computers. The ARM64 build is selected automatically on ARM64 Windows.

## Install

Open PowerShell and run:

```powershell
irm https://jcode.sh/install.ps1 | iex
```

The installer:

1. Detects x64 or ARM64.
2. Downloads the matching asset from the official GitHub release.
3. Verifies it against the release's `SHA256SUMS` file.
4. Installs immutable, stable, and launcher copies under `%LOCALAPPDATA%\jcode`.
5. Adds `%LOCALAPPDATA%\jcode\bin` to the user `PATH`.

Alacritty installation and the global launch hotkey are optional and are no longer installed automatically. To request both explicitly:

```powershell
$script = [scriptblock]::Create((irm https://jcode.sh/install.ps1))
& $script -ConfigureAlacritty -ConfigureHotkey
```

Jcode can also offer these options interactively after launch.

If a release does not contain a matching prebuilt Windows asset, the installer
fails with an actionable message rather than silently starting a long build.
To explicitly allow a source build:

```powershell
$script = [scriptblock]::Create((irm https://jcode.sh/install.ps1))
& $script -BuildFromSource
```

Source builds require Git, Rust, and Visual Studio 2022 Build Tools with the
**Desktop development with C++** workload.

### Install paths

- Launcher: `%LOCALAPPDATA%\jcode\bin\jcode.exe`
- Stable binary: `%LOCALAPPDATA%\jcode\builds\stable\jcode.exe`
- Versioned binary: `%LOCALAPPDATA%\jcode\builds\versions\<version>\jcode.exe`
- User data and configuration: `%USERPROFILE%\.jcode`

### Verify an installation

```powershell
jcode --version
Get-Command jcode
Get-FileHash (Get-Command jcode).Source -Algorithm SHA256
```

Compare the hash with `SHA256SUMS` on the matching [GitHub release](https://github.com/1jehuang/jcode/releases/latest).

After Authenticode signing is enabled, this must report `Valid`:

```powershell
Get-AuthenticodeSignature (Get-Command jcode).Source | Format-List Status,StatusMessage,SignerCertificate
```

## Microsoft Defender and SmartScreen

Two different Windows warnings are commonly confused:

- **Microsoft Defender SmartScreen** shows messages such as “Windows protected your PC” when a downloaded application is unsigned or has not accumulated enough publisher reputation. Authenticode signing with a trusted, timestamped certificate is the primary fix. Reputation still builds over time for a new publisher identity.
- **Microsoft Defender Antivirus** reports a named threat or suspicious behavior. Signing helps establish provenance, but a heuristic false positive must also be submitted to Microsoft with the exact signed file and SHA-256 hash.

Do not tell users to disable Defender, add exclusions, or bypass a named malware detection. First verify the release URL, checksum, and Authenticode signature. If a correctly signed official build is still detected, submit it through the [Microsoft Security Intelligence file submission portal](https://www.microsoft.com/wdsi/filesubmission) as a software developer false positive.

### Heuristic-trigger reduction already in place

The Windows setup is deliberately designed to avoid unnecessary suspicious behavior:

- Release downloads are verified against `SHA256SUMS`.
- Optional terminal and global-hotkey setup requires explicit consent.
- The old hidden VBScript startup trampoline has been removed.
- The hotkey listener uses a direct PowerShell shortcut with `RemoteSigned`, not `ExecutionPolicy Bypass`.
- Release binaries are built on GitHub-hosted Windows runners and tested before publication.

## Enable Authenticode signing

The release workflow supports [Azure Artifact Signing](https://azure.microsoft.com/products/artifact-signing) with GitHub OIDC. This keeps the certificate private key in Microsoft's managed signing service instead of exporting it into a GitHub secret.

This is a one-time owner setup and may require Azure billing and organization or identity verification:

1. Create an Artifact Signing account and a public-trust certificate profile.
2. Create a Microsoft Entra application or managed identity with a federated credential for `1jehuang/jcode` GitHub Actions.
3. Grant it the **Artifact Signing Certificate Profile Signer** role on the certificate profile.
4. Add these GitHub Actions secrets:
   - `AZURE_CLIENT_ID`
   - `AZURE_TENANT_ID`
   - `AZURE_SUBSCRIPTION_ID`
5. Add these GitHub Actions variables:
   - `WINDOWS_SIGNING_ENDPOINT`, for example `https://eus.codesigning.azure.net/`
   - `WINDOWS_SIGNING_ACCOUNT`
   - `WINDOWS_SIGNING_CERTIFICATE_PROFILE`
6. Push a test tag and confirm the `Sign and publish Windows assets` job signs both executables and that `Get-AuthenticodeSignature` returns `Valid`.
7. Leave `WINDOWS_SIGNING_REQUIRED` unset or set it to `true`. Signing is required by default for Windows assets, so missing configuration or a signing outage omits Windows from that release while every other successful platform can still publish. Setting it to `false` is an explicit emergency override and is not suitable for an official Windows release.

The workflow applies SHA-256 Authenticode signatures and RFC 3161 timestamps before packaging, checksum generation, and release upload. Both x64 and ARM64 executables are signed on a supported x64 Windows signing runner.

Do not describe the Defender and SmartScreen rollout as complete until signing enforcement is active and a public release has a valid signature.

## Release acceptance checklist

For every release that changes Windows behavior:

- [ ] Windows x64 CI build and targeted tests pass.
- [ ] Windows lifecycle end-to-end tests pass.
- [ ] x64 and ARM64 installer verification passes.
- [ ] Both `.exe` files have a valid, timestamped Authenticode signature.
- [ ] `SHA256SUMS` contains both Windows executables and archives.
- [ ] A clean Windows 11 machine installs, launches, updates, and uninstalls successfully.
- [ ] Defender Antivirus reports no named detection on the signed release.
- [ ] SmartScreen identifies the expected publisher. Any low-reputation warning is tracked separately from malware detection.
- [ ] The website Windows button points to a published asset and contains no preview or work-in-progress wording.
- [ ] Release notes mention material Windows fixes or limitations.

## Continuous integration

Windows is covered by:

- `.github/workflows/ci.yml`: release build, test compilation, targeted platform tests, runtime smoke tests, lifecycle end-to-end tests, installer verification, and PowerShell syntax checks.
- `.github/workflows/windows-smoke.yml`: manually dispatchable x64 and ARM64 smoke validation.
- `.github/workflows/release.yml`: x64 and ARM64 builds, managed signing required for Windows publication, signature verification, packaging, and checksums. Every platform and architecture publishes independently, so a Windows failure cannot block any successful Unix asset.

## Architecture notes

Unix domain sockets are replaced by Windows named pipes under `crates/jcode-base/src/transport/windows.rs`. Platform-specific filesystem, process, update, and replacement behavior is selected at compile time with `#[cfg(windows)]`, so Windows support does not add runtime branching to Unix builds.

Windows launch-hotkey setup is implemented in `crates/jcode-setup-hints/src/windows_setup.rs` and is only installed after explicit user consent.

## Reporting Windows problems

Include the following in a GitHub issue:

- Windows edition, version, and architecture
- Jcode version from `jcode --version`
- Installation method
- Terminal and PowerShell version (`$PSVersionTable.PSVersion`)
- Exact Defender or SmartScreen message
- Defender threat name, if one was shown
- SHA-256 from `Get-FileHash`
- Authenticode status from `Get-AuthenticodeSignature`

Do not upload private configuration, credentials, session transcripts, or `.jcode` authentication files.
