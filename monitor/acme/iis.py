import os
import subprocess
import sys
import tempfile


def is_windows():
    return sys.platform.startswith("win")


INSTALL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$pfxPath = $env:CM_PFX_PATH
$password = $env:CM_PFX_PASSWORD
$hostHeader = $env:CM_HOST_HEADER
$port = [int]$env:CM_PORT
$siteName = $env:CM_SITE_NAME

$secure = ConvertTo-SecureString -String $password -AsPlainText -Force
$cert = Import-PfxCertificate -FilePath $pfxPath -CertStoreLocation Cert:\LocalMachine\My -Password $secure
$thumbprint = $cert.Thumbprint

Import-Module WebAdministration
$match = "*:$port`:$hostHeader"
$binding = Get-WebBinding -Name $siteName -ErrorAction SilentlyContinue | Where-Object { $_.protocol -eq 'https' -and $_.bindingInformation -eq $match }
if (-not $binding) {
    New-WebBinding -Name $siteName -Protocol https -Port $port -HostHeader $hostHeader -SslFlags 1
    $binding = Get-WebBinding -Name $siteName | Where-Object { $_.protocol -eq 'https' -and $_.bindingInformation -eq $match }
}
if (-not $binding) { throw "Impossible de creer le binding HTTPS pour $hostHeader sur le site $siteName" }
$binding.AddSslCertificate($thumbprint, 'My')
Write-Output $thumbprint
"""


def install_certificate_iis(pfx_bytes, password, host_header, port, site_name):
    if not is_windows():
        raise RuntimeError("L'installation IIS n'est possible que sous Windows.")
    fd, pfx_path = tempfile.mkstemp(suffix=".pfx")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(pfx_bytes)
        env = dict(os.environ)
        env.update({
            "CM_PFX_PATH": pfx_path,
            "CM_PFX_PASSWORD": password,
            "CM_HOST_HEADER": host_header,
            "CM_PORT": str(port),
            "CM_SITE_NAME": site_name,
        })
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", INSTALL_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        try:
            os.unlink(pfx_path)
        except OSError:
            pass
    if result.returncode != 0:
        raise RuntimeError(f"Échec de l'installation IIS : {result.stderr.strip() or result.stdout.strip()}")
    thumbprint = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not thumbprint:
        raise RuntimeError("L'installation IIS n'a pas renvoyé d'empreinte de certificat.")
    return thumbprint
