# Database Backup, Encryption and Recovery Guide

ImageTrace stores its case database in `<data_root>\database\imagetrace.sqlite` (by default
`D:\imagetrace\data\database\imagetrace.sqlite`) and writes all backups to `<data_root>\backups\`.

> **Important:** ImageTrace **never** deletes backups automatically. Database backups contain the
> SQLite metadata, case notes, and history, while original evidence images stay in
> `<data_root>\cases\<case-id>\originals\`. Back up the entire `data\` folder (including `cases\`)
> to an offline, encrypted drive for full disaster recovery.

---

## 1. Backup formats
1. **Plain SQLite backup (`imagetrace-<label>-<UTC>.sqlite`):**
   - Consistent online snapshot via SQLite's backup API, checked with `PRAGMA integrity_check`,
     and paired with a `<filename>.sha256` sidecar.
   - **Not encrypted** — protect `data\backups\` with Windows user account security and BitLocker
     full-disk encryption.
2. **Encrypted backup (`imagetrace-<label>-<UTC>.sqlite.itbak`):**
   - Encrypted with **AES-256-GCM** using a 256-bit key derived from your passphrase via **scrypt**
     (`N = 65536`, `r = 8`, `p = 1`, random 16-byte salt, random 12-byte nonce).
   - The 40-byte container header is bound as GCM Additional Authenticated Data (AAD), so any
     modification to the header or ciphertext causes decryption to fail cleanly.
   - Paired with a `<filename>.sha256` sidecar.
   - **No recovery backdoor:** ImageTrace never stores or logs your backup passphrase. If you lose
     the passphrase, the encrypted backup **cannot** be recovered. Store passphrases in a trusted
     offline password manager or sealed recovery record.

---

## 2. Restoring inside ImageTrace
1. Open **Settings → Database → Restore from backup…**.
2. Choose a `.sqlite` or `.sqlite.itbak` file from `data\backups\`.
3. If the file is encrypted, enter its passphrase when prompted.
4. ImageTrace verifies the `.sha256` sidecar, decrypts (if encrypted), runs `PRAGMA integrity_check`,
   saves a `pre-restore` backup of your current database first, restores the chosen backup, and
   runs any forward schema migrations automatically.

---

## 3. Recovering if the live database is corrupt or cannot open
If `imagetrace.sqlite` is damaged so ImageTrace starts in database-error mode:

1. Quit ImageTrace.
2. Copy the damaged `data\database\imagetrace.sqlite` to `data\backups\damaged-copy.sqlite` so
   nothing is discarded.
3. Verify your chosen backup's SHA-256 against its `.sha256` sidecar (`certutil -hashfile <file> SHA256`).
4. **If restoring a plain `.sqlite` backup:** copy it over `data\database\imagetrace.sqlite` and
   relaunch ImageTrace.
5. **If restoring an encrypted `.sqlite.itbak` backup:** run the built-in recovery helper from the
   project venv (or any Python environment with `cryptography`):

```powershell
D:\imagetrace\venv\Scripts\python.exe -c "import getpass; from pathlib import Path; from imagetrace.backup import restore_corrupt_database_file; pw = getpass.getpass('Backup passphrase: '); restore_corrupt_database_file(Path(r'D:\imagetrace\data\database\imagetrace.sqlite'), Path(r'D:\imagetrace\data\backups\YOUR_BACKUP.sqlite.itbak'), Path(r'D:\imagetrace\data\backups'), passphrase=pw)"
```

---

## 4. Independent container format specification (`ITBAK1`)
You do not need ImageTrace installed to decrypt a `.sqlite.itbak` file later. The binary layout is:

| Offset | Length | Field |
|-------:|-------:|-------|
| 0 | 8 bytes | Magic `ITBAK1\x00\x01` |
| 8 | 1 byte | `n_exp` (`uint8`, `N = 2 ** n_exp`, default `16`) |
| 9 | 1 byte | `r` (`uint8`, default `8`) |
| 10 | 1 byte | `p` (`uint8`, default `1`) |
| 11 | 1 byte | `reserved` (`uint8`, `0`) |
| 12 | 16 bytes | Random `salt` |
| 28 | 12 bytes | Random AES-GCM `nonce` |
| 40 | rest | AES-256-GCM ciphertext + 16-byte tag (`AAD = bytes[0:40]`) |

Standalone decryption snippet (requires only Python + `cryptography`):
```python
import getpass, struct
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

blob = Path("imagetrace-manual-YYYYMMDDTHHMMSSZ.sqlite.itbak").read_bytes()
header = blob[:40]
magic, n_exp, r, p, reserved, salt, nonce = struct.unpack(">8sBBBB16s12s", header)
assert magic == b"ITBAK1\x00\x01" and reserved == 0
pw = getpass.getpass("Passphrase: ").encode("utf-8")
key = Scrypt(salt=salt, length=32, n=1 << n_exp, r=r, p=p).derive(pw)
Path("recovered.sqlite").write_bytes(AESGCM(key).decrypt(nonce, blob[40:], header))
```
