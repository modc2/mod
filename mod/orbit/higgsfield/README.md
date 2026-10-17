# higgsfield

Higgsfield AI's generation API as one mod. [Higgsfield](https://higgsfield.ai)
does cinematic generation — **Soul** for photoreal text-to-image, **DoP** for
image-to-video with named camera motions (crash zoom, dolly, 360 orbit, …),
**Speak** for talking avatars. This module wraps the platform API behind the
fleet's usual shape: BYOK keys off-tree, one client answering both the `m` CLI
and a small REST server, and a `raw` escape hatch for anything the named
functions don't cover.

## The async cycle

Generation upstream is asynchronous. Every submit (`image`, `video`, `speak`)
returns a **job-set id**, not an artifact. Poll it:

```
m higgsfield/image "neon street, rain, 35mm"     → { id: <job_set_id>, … }
m higgsfield/job id=<job_set_id>                 → status + artifact URLs when completed
m higgsfield/wait id=<job_set_id>                → blocks until terminal (timeout=600)
```

Terminal statuses: `completed`, `failed`, `canceled`, `nsfw`.

## Keys (BYOK)

Every call spends the caller's own Higgsfield credits. Get an
`hf-api-key` + `hf-secret` pair at <https://cloud.higgsfield.ai>, then:

```
m higgsfield/set_key key=hf-… secret=…
```

Resolution order: per-call kwargs → `HIGGSFIELD_API_KEY` / `HIGGSFIELD_SECRET`
env → `~/.mod/higgsfield/key.json` (written 0600). Nothing private is ever
stored in this repo.

## Functions

| fn | what |
|---|---|
| `info` | module description, routes, whether a key resolved |
| `set_key key= secret=` | store the key pair, 0600, off-tree |
| `key` | whether a key resolved and from where (never the key itself) |
| `image prompt=…` | Soul text-to-image → job-set |
| `video image_url=… motion=… prompt=…` | DoP image-to-video → job-set |
| `speak image_url=… audio_url=…` | talking avatar → job-set |
| `motions` | the named camera motions DoP accepts |
| `job id=` | poll one job-set |
| `wait id= timeout= interval=` | block until the job-set is terminal |
| `raw path= method= body=` | any `platform.higgsfield.ai` route |
| `serve` / `kill` | REST server on :51210 / stop it |
| `test` | no-network sanity check |

## REST

`m higgsfield/serve` (or `python mod.py serve`) listens on `:51210`:

```
GET  /            info          GET  /health        ok
GET  /motions     camera moves  GET  /job?id=…      poll
POST /image       {prompt,…}    POST /video         {image_url, motion, …}
POST /speak       {image_url, audio_url}
POST /raw         {path, method, body}
```

Routes also answer under the `/higgsfield` prefix, so the fleet router can
pass the path through unstripped.

## Env

| var | default |
|---|---|
| `PORT` | 51210 |
| `HIGGSFIELD_DIR` | `~/.mod/higgsfield` |
| `HIGGSFIELD_API_KEY` / `HIGGSFIELD_SECRET` | — (checked before the keystore) |
| `HIGGSFIELD_UPSTREAM` | `https://platform.higgsfield.ai` |

## Caveat

Upstream endpoint paths follow <https://docs.higgsfield.ai> and are collected
in one `PATHS` dict at the top of `mod.py`. If Higgsfield moves an endpoint or
adds a model family, that dict is the only thing to touch — and `raw` reaches
any route in the meantime.
