# Tabbie Team protocol (tt1)

How devices, Macs and the relay trust each other. The relay is **untrusted**: it routes and stores, but every
decision that matters is made on a Mac or a device with its own keys.

## Keys
- Every Mac and every browser device has two P-256 keys: **sign** (ECDSA/SHA-256, signatures are raw r||s)
  and **ecdh** (for receiving team keys). Browser keys are WebCrypto, non-extractable, in IndexedDB. Mac keys
  are in `~/.hermes/team/mac.json` (0600).
- `kid` = base64url(SHA-256(sign_pub))[:22]. **Fingerprint** = first 60 bits of SHA-256(sign_pub) in Crockford
  base32, `XXXX-XXXX-XXXX`. People compare fingerprints, never kids.
- Everything signed is `JSON.stringify([...])` of a fixed array, first item naming the kind (`tt1-cert`, …).

## Trust
- **A Mac is the root of trust for its owner.** It's linked once with a pairing code typed into the signed-in
  web page (passkey session), and the page shows the Mac's fingerprint to compare with the terminal.
- **Browser devices** register their public keys, then the owner approves them **on the Mac** by typing the
  fingerprint the browser shows (Settings → Team, the "A new device wants in" sheet, or `bin/tt approve CODE`). The Mac signs a certificate
  `["tt1-cert", kid, owner, "browser", name, sign_pub, ecdh_pub, issued_at]` and keeps the device in its own
  allowlist. The relay's list is never trusted on its own.
- **The other person's Mac** is trusted only after its fingerprint is pinned (`tt trust OWNER CODE`), compared in
  person or over a call. Their devices are trusted through certificates signed by that pinned Mac.
- **Invites** (`tt invite NAME` on a linked Mac, or Settings → Team → Invite a teammate) replace both steps
  above for a new teammate's Mac. The code is `TT1-` + b64url JSON `{r: relay, u: invitee, s: secret, o: inviter,
  k: inviter Mac kid}`, 32-byte random secret, one use, 24 hours. The relay stores only `sha256(token)` where
  `token = HMAC(secret, "tt1-invite-token")`, so it never learns the secret. The joining Mac (`tt join CODE`)
  sends the token plus `proof = HMAC(secret, canon(["tt1-invite", sign_pub, ecdh_pub]))`; the relay links it to
  the invited account straight away. The joining Mac pins the inviting Mac by `k` (checking `kid_of(sign_pub)`
  from the directory). The inviting Mac checks the proof (`GET /api/macs/invites`) before pinning the new Mac
  and wrapping the team key to it. A relay that forges a join can't produce the proof, so it gets no key.
- **Browsers** pin each owner's Mac the first time they see it (their own Mac when linking it) and accept other
  devices only with a certificate from a pinned Mac.
- **Removal**: the Mac signs `["tt1-revoke", kid, owner, at]`, then rotates the team key. A removed device's
  earlier messages still show, flagged "(removed)"; anything it signs after `at` is rejected and never runs.

## Team key
- AES-256-GCM, made on the first linked Mac. Given to a device as a **wrap**: ephemeral ECDH P-256 with the
  device's ecdh key → HKDF-SHA256 (salt = ephemeral public key, info `tt1-wrap`) → AES-GCM over the key, AAD
  `key_id|recipient_kid`. Each wrap is signed by the wrapping Mac:
  `["tt1-wrap", key_id, recipient_kid, epk, iv, ct, wrapper_kid]`; devices accept wraps only from a trusted Mac.
- `tt rotate` makes a new key and wraps it to every trusted device; old keys stay so history stays readable.

## Events
```
{ v:1, room, type, author, kid, key_id, nonce, ts, prev, iv, ct, sig }
```
- `ct` = AES-GCM(team key, JSON payload), AAD = `["tt1-ev", room, type, author, kid, key_id, nonce, ts, prev]`.
- `sig` covers `["tt1-ev", room, type, author, kid, key_id, nonce, ts, prev, iv, ct]`.
- `prev` = base64url(SHA-256(previous event body as stored))[:22]: the sender's view of history.
- The relay (first filter): shape, room, author = signed-in user, kid = one of that user's approved keys, valid
  signature, ts within 5 min, nonce never seen. It stores the envelope and assigns `seq`.
- A Mac (the real check): its own trust, signature, team key, decryption. A nonce seen again under a different
  `seq` is a replay. `prompt` / `approval` events run only if: author is this Mac's owner, signed by a device
  this Mac approved (not removed), ts within 2 minutes, never run before.

## Relays
- Any server can be a relay if it speaks this protocol. Tabbie checks an address before using it:
  `GET /.well-known/tabbie-team` must answer `{"protocol": "tt1", "version": 1, "name": "<team name>"}`. The
  name is only a label; trust still comes from linking the Mac and comparing fingerprints.
- Relay API the Mac uses: `POST /api/macs/pair`, `/api/mac/challenge`, `/api/mac/token` (signs
  `["tt1-auth", challenge, relay_origin]`), `GET /api/devices`, `POST /api/devices/:kid/approve|revoke`,
  `GET|POST /api/wraps`, `GET /api/directory`, and a WebSocket per room at `/api/rooms/:room/ws`.
- What a Mac shares: every chat in the projects picked in Settings → Team (`~/.hermes/team/config.json`).
  Each chat maps to a random room id, so the relay never sees Tabbie's ids, names or text.
