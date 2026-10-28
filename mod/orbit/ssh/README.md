# ssh — server-side SSH key vault

Private keys are stored on the server at `~/.mod/ssh/keys/`, encrypted at
rest in **OpenSSH's own passphrase-protected format** (bcrypt KDF, done by
`ssh-keygen -p`). There is no custom crypto: the password check, encryption
and rotation are all OpenSSH primitives, and every stored key file is a
normal encrypted OpenSSH key — portable to any machine with `ssh -i`.

The vault never stores a password and never writes a plaintext key to disk:
per-use decryption happens into a `0600` tempfile on `/dev/shm` (RAM) that
is deleted before the call returns.

## Operations

| op | needs password | what it does |
|---|---|---|
| `add name= private_key= password= [current_password=]` | sets it | import a key you already have; re-encrypts it under your password |
| `generate name= password= [type=ed25519] [comment=]` | sets it | mint a new keypair, encrypted from birth |
| `keys` | no | list metadata + public keys |
| `pubkey name=` | no | public key line for `authorized_keys` |
| `verify name= password=` | yes | check a password without exporting anything |
| `export name= password= [encrypted=true]` | yes | return the private key (plaintext, or the at-rest ciphertext) |
| `exec name= password= host= cmd= [user=root] [port=22]` | yes | run a command over ssh using the vault key, decrypted for that call only |
| `passwd name= old_password= new_password=` | yes | rotate the encryption password |
| `remove name=` | no | delete key + metadata |

Passwords must be ≥ 5 characters (OpenSSH's own minimum). Key names are
`[A-Za-z0-9._-]`, max 64 chars.

## Example

```bash
# import the key you have
m ssh/add name=mybox private_key="$(cat ~/old_key)" password=hunter22

# use it without ever handling the plaintext yourself
m ssh/exec name=mybox password=hunter22 host=1.2.3.4 cmd='uptime'

# hand out the public half
m ssh/pubkey name=mybox
```

`SSH_STORE_DIR` overrides the store location (useful for tests).
