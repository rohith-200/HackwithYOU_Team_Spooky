# Ghost Ops

Track 1 implementation for Hack with YOU: Halloween Edition.

This repository is being built in three lanes:

- **CrewAI** coordinates the supervised agent swarm.
- **You.com** grounds the swarm with live search and page contents.
- **Parasail** provides inference, embeddings, and structured Commander output.
- **One** performs the real Discord escalation action.

## Person B integration lane

The current scaffold provides:

- `src/llm.py` - Parasail's OpenAI-compatible client, CrewAI LLM wrapper,
  bounded retry/backoff, JSON output, and embeddings.
- `src/tools/youcom.py` - You.com Search and Contents clients using the
  required `X-API-Key` header, compact citations, and short-lived caching.
- `src/triage.py` - sector serialization, cosine similarity, and embedding
  based calm/danger triage.
- `src/commander_output.py` - strict `contain` / `evacuate` / `observe`
  Commander schema and validation.

## Local setup

Use Python 3.12. CrewAI is not supported by the event plan on Python 3.14.

```bash
python3.12 -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\\Scripts\\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env           # Windows: Copy-Item .env.example .env
```

Fill the event credentials in `.env`. Never commit `.env` or paste keys into
Discord.

## Person B smoke check

The integration modules do not make network requests on import. A later
integration test should provide event credentials and verify:

1. `GET https://api.parasail.io/v1/models` returns the configured model.
2. Parasail answers a simple test prompt.
3. You.com search returns short snippets with source URLs.
4. Outbreak and control fixtures classify as hot and calm respectively.
5. Commander output validates against the shared schema.

The scenario tick, decision, and UI event contracts from the design document
must not be renamed without notifying the other two team members.
