# iPad SSH setup: qualification stage

SSH was verified listening on the current Mac. Use your Mac’s local hostname
and username in place of `MAC_HOST.local` and `MAC_USER`. Device-specific details
are kept in the ignored `.private/IPAD-SSH-SETUP.md` local copy. The user’s iPad
is on the same LAN and Apple Account.
No new listener, account, password, or SSH authorization has been installed.

## On the iPad

1. Create a new Shortcut named **AFM iPad Worker**. Keep the existing AFM Bridge
   shortcuts unchanged.
2. Add **Run Script over SSH**. Expand its connection settings and enter host
   `MAC_HOST.local`, port `22`, user `MAC_USER`.
3. Choose **SSH Key** authentication and create/select a dedicated key for this
   worker. Share/copy its **public key**. Provide only the single public-key line
   (starts with `ssh-ed25519`, `ssh-rsa`, or another SSH public-key type), never a
   private key or password. The exact labels can vary with iPadOS.
4. Do not run it yet. The Mac must first authorize that key with a forced command.

Before approving the host identity on iPad, read your Mac’s public ED25519
fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`; replace this
placeholder with the verified result:
`SHA256:VERIFY_YOUR_MAC_HOST_KEY`.
A different negotiated host-key type can have a different fingerprint; verify
that type separately rather than accepting an unexpected key blindly.

## Mac enrollment (pending the public key)

Validate the supplied public key with `ssh-keygen`. Add an entry to the existing
`~/.ssh/authorized_keys` preserving every other entry, with these options:

```
restrict,command="/ABSOLUTE/REPOSITORY/PATH/scripts/ipad-worker-ssh" PUBLIC_KEY_LINE
```

The forced command ignores `SSH_ORIGINAL_COMMAND` and accepts a single bounded
JSON message on stdin. `restrict` disables forwarding, PTY, agent forwarding,
X11 forwarding, and user rc execution for this key. It grants access only to the
qualification queue through this wrapper, not an interactive shell. Do not reuse
an existing general-purpose key: another unrestricted authorization for the same
key could defeat this intended restriction.

## Qualification protocol

One Cloud job is prepared locally; preparing it does not call Apple. Adapter:
`afm_hle.ipad_worker`, private DB `.private/ipad-qualification.sqlite3`.
This stage is synthetic-only and refuses HLE payloads. It currently uses its own
local ledger; integrating device events into afm-gateway reporting and central
HLE allocation remains required before benchmark rollout.

Once the public key is installed, configure the Shortcut as follows:

1. **Text** action containing `{"operation":"next"}`.
2. **Run Script over SSH** with that text as its **Input** (stdin). Script text
   can be `afm-ipad-worker`; the forced command ignores it. Never interpolate
   question or answer text into the shell script field.
3. Parse returned JSON with **Get Dictionary from Input**. If `status` is not
   `job`, stop. Keep the dictionary in a Job variable.
4. Verify `model` equals `afm-cloud`. Run the existing **AFM Bridge - Cloud**
   shortcut on the iPad with the Job's `prompt` as its input. No fallback.
5. Build a dictionary containing `operation=result`, `job_id`, `payload_sha256`,
   `model`, `status=done`, the returned `text`, and JSON-null `error_code`.
   Serialize using Shortcuts' dictionary/JSON facilities, not manual string
   concatenation; answers may contain quotes and newlines.
6. Save that result JSON privately on the iPad before attempting upload. Send it
   as stdin through a second **Run Script over SSH** action using the same key.
7. Show the returned acknowledgement. `status=recorded` means the response was
   saved; `exact_match=true` means it returned exactly `TEST_OK`.

The first stage runs one synthetic call. An interrupted Shortcut leaves its job
in flight and blocks further claims; it is never automatically rerun. A repeated
identical result upload is safe. Preserve the saved JSON if acknowledgement is
lost, and retry only its upload. Setup/model errors require explicit review.

Do not run the complete recipe before verifying the SSH Input action passes
stdin correctly on this iPad. A connection test and actual model call will be
checked separately during enrollment. Credentials stay out of Git and the
existing Mac benchmarks continue unchanged.
