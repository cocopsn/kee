# Syncthing pair-up — Alienware ↔ Auctorum

Two-way mirror of `vault/` so both nodes index the same Markdown.
ChromaDB on Auctorum can re-index Coco's edits even when he's coding
on the Alienware. The Alienware vault watcher already triggers
`indexer` on changes — Syncthing just keeps the bytes in sync.

## Prerequisites

- Both nodes have `syncthing` installed.
  - Auctorum: already done by `provision.sh`.
  - Alienware: `winget install syncthing.syncthing`
    (or download from <https://syncthing.net/downloads/>).
- Both nodes are on Tailscale (no need to expose Syncthing's UI to the
  internet).

## Steps

### 1. Start Syncthing on Auctorum (one-time)

```bash
ssh cocopsn@100.121.31.99
sudo systemctl enable --now syncthing@cocopsn
# (already enabled by provision.sh; this is just confirm)
```

### 2. Tunnel the Auctorum UI

From the Alienware:

```powershell
ssh -L 8385:localhost:8384 cocopsn@100.121.31.99 -N
```

Leave that running. Open <http://localhost:8385> — that's Auctorum's
Syncthing UI.

### 3. Get device IDs

- **Auctorum UI** (the one you just tunnelled): _Actions → Show ID_.
  Copy.
- **Alienware UI** (open Syncthing on Windows; default <http://127.0.0.1:8384>):
  _Actions → Show ID_. Copy.

### 4. Add each device on the other

- On Alienware UI: _Add Remote Device_, paste Auctorum's ID, name it
  `auctorum`, save.
- On Auctorum UI: _Add Remote Device_, paste Alienware's ID, name it
  `alienware`, save.

Within ~30s each side will show the other as connected.

### 5. Share the vault folder

On the Alienware UI:

1. _Add Folder_
2. Folder Path: `D:\Kee\vault`
3. Folder ID: `kee-vault`
4. Folder Label: `Kee vault`
5. Sharing tab → check `auctorum`
6. Save

A pop-up appears on Auctorum's UI asking to accept. Accept; set the
folder path to `/home/cocopsn/kee-vault`.

### 6. Wait for initial sync

20 files, all small. Should be < 30 seconds.

### 7. Verify

On the Alienware:

```powershell
echo "test sync" >> D:/Kee/vault/notes/sync_test.md
```

Within 30s, on Auctorum:

```bash
ssh cocopsn@100.121.31.99 'cat ~/kee-vault/notes/sync_test.md 2>/dev/null'
```

Should print "test sync".

## Conflict policy

Syncthing's default is _Latest version wins_ — fine for vault content.
If Coco has both nodes editing the same file in flight, the older one
gets renamed `<file>.sync-conflict-...md` and a notification fires on
both UIs.

## Operational notes

- The Alienware vault watcher and the Auctorum cron re-index will both
  fire on changes — that's redundant but harmless (ChromaDB upserts
  are idempotent on `chunk_id`).
- Syncthing uses ports 22000 (TCP sync) + 21027 (UDP discovery). Both
  already opened by `provision.sh` for the Tailscale CGNAT range.
- To bring up the UI permanently on Auctorum without an SSH tunnel,
  add this to `~/.config/syncthing/config.xml` and restart syncthing:
  ```xml
  <gui>
    <address>0.0.0.0:8384</address>
  </gui>
  ```
  Then `sudo ufw allow from 100.64.0.0/10 to any port 8384`.
  The default is loopback-only for safety.
