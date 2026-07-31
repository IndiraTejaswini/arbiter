"""Central settings, read once from the environment. See docker-compose.yml
for the default local values these fall back to."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARBITER_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://arbiter:arbiter@localhost:5432/arbiter"
    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "arbiter"
    minio_secret_key: str = "arbiter123"
    minio_bucket: str = "arbiter-artifacts"
    minio_secure: bool = False

    rulepack_dir: Path = REPO_ROOT / "rulepacks" / "amex"

    conformal_alpha: float = 0.05
    conformal_min_n: int = 100

    # Qwen2.5-VL via a local Ollama daemon (arbiter.ingest.extract_vlm).
    # Extraction degrades to the OCR/native path if unreachable -- CLAUDE.md
    # #9: evidence degrades, never rejected; that principle applies to the
    # extraction pipeline's own availability too.
    ollama_base_url: str = "http://localhost:11434"
    vlm_model: str = "qwen2.5vl:7b"

    max_artifact_bytes: int = 25 * 1024 * 1024

    cors_origins: list[str] = ["http://localhost:3000"]

    # Deployment environment. Anything other than "dev" is treated as a
    # real deployment and enables the startup guards in
    # `validate_for_environment()` below -- which REFUSE TO BOOT rather
    # than run with a known-insecure default. Fail closed at startup is
    # the only place a misconfiguration is still cheap to fix.
    env: str = "dev"

    # -- Authorization (arbiter.auth) --
    # HMAC secret for bearer-token issuance/verification. The default is
    # deliberately obviously-insecure so it is impossible to mistake for a
    # production value; any real deployment MUST override
    # ARBITER_AUTH_SECRET with a high-entropy secret from a real secret
    # store, not baked into source control.
    auth_secret: str = "dev-insecure-secret-change-me"
    auth_token_ttl_seconds: int = 3600

    # The unauthenticated `POST /v1/auth/dev-token` route mints a bearer
    # token for ANY role -- including ADMIN -- with no identity check
    # behind it. It exists so the frontend and demo scripts have something
    # to call without a real IdP. It is OFF unless explicitly enabled, and
    # `validate_for_environment()` refuses to boot if it is enabled outside
    # `env=dev`. Previously this route was registered unconditionally and
    # its safety depended on someone remembering to delete it before
    # deploying; a route secured by a deletion reminder is not secured.
    enable_dev_auth: bool = False

    # -- Audit signing keys (arbiter.audit.sign) --
    # Active signing key: a hex-encoded 32-byte Ed25519 seed. If unset, a
    # fresh ephemeral key is generated and every signature made this run
    # becomes unverifiable on the next restart -- fine for a laptop demo,
    # never for anything whose audit trail needs to outlive one process.
    signing_key_seed: str | None = None
    signing_key_epoch: int = 0
    # Every signing key epoch this deployment has ever used (JSON: {"0":
    # "<hex seed>", "1": "<hex seed>"}), so a signature made before a
    # rotation is still verifiable after one -- rotating never invalidates
    # history. Falls back to {signing_key_epoch: signing_key_seed} if unset.
    signing_key_ring: dict[str, str] = {}

    # -- Transparency-log identities (arbiter.provenance) --
    # THREE separate signing identities by design (A1): the audit-event
    # signer above, the transparency-log operator, and the TSA. Compromising
    # one must not compromise the others. All three must be persistent for
    # a durable log -- an ephemeral log-operator key makes every signed tree
    # head already in `merkle_batch` unverifiable after a restart, which
    # silently voids the non-backdating proof ADEC exists to provide.
    log_operator_key_seed: str | None = None
    tsa_key_seed: str | None = None

    # -- Crypto-shredding master key (arbiter.privacy.shredding) --
    # Per-subject Fernet keys are wrapped under this KEK before being
    # written to `subject_key.wrapped_key`, so a database compromise alone
    # never yields plaintext PII. Must be a urlsafe-base64 32-byte Fernet
    # key. When unset the vault stays in-memory-only and warns.
    key_encryption_key: str | None = None

    # -- PAN tokenisation (arbiter.privacy.tokenize) --
    # HMAC key producing the irreversible surrogate that replaces a card
    # number BEFORE it is persisted. This is what keeps `evidence_node`
    # -- and therefore every projection, export, and downstream service
    # that reads it -- out of PCI DSS CDE scope. Must be independent of
    # the audit signing key; deriving one from the other couples two
    # rotation schedules that should be separate.
    pan_tokenization_key: str | None = None

    # -- Artifact object storage (arbiter.storage) --
    # Reg E 12 CFR 1005.11(d) obliges the issuer to disclose "the
    # consumer's right to request the documents relied on" -- which
    # requires actually retaining them. Artifact bytes are written here on
    # upload and served back by GET /v1/artifacts/{id}/content.
    artifact_storage_enabled: bool = True
    # Filesystem fallback used when MinIO/S3 is unreachable or `minio` is
    # not installed. Never a substitute for WORM object storage in
    # production -- it exists so a laptop demo retains artifacts instead of
    # silently discarding them, which is what happened before.
    artifact_local_dir: Path = REPO_ROOT / ".artifact-store"

    # -- Conformal calibration (arbiter.decision.conformal) --
    # Minimum real calibration samples required, per reason code, before
    # the abstention gate may auto-resolve ANYTHING. Below this the gate
    # escalates every case: an uncalibrated conformal gate has no coverage
    # guarantee to offer, and a fabricated one is worse than none.
    conformal_require_real_calibration: bool = True
    # -- Audit sampling (arbiter.decision.review_sampling) --
    # Fraction of AUTO-RESOLVED cases routed to a human anyway, so the
    # calibration pool sees the region of the distribution the escalation
    # path never visits. Without it, analyst reviews come only from the
    # escalated (high-nonconformity) tail, and feeding that tail back
    # inflates the conformal quantile -- making the gate MORE permissive
    # the more human review is done. Set to 0.0 to disable, which the gate
    # will warn about because it re-introduces the bias.
    review_audit_rate: float = 0.05
    # Keyed so selection cannot be ground for: with an unkeyed hash a party
    # able to influence a case identifier could search for one that lands
    # outside the audit window.
    review_sampling_salt: str = "arbiter-audit-sampling-v1"

    # Contradiction severities that hard-block auto-resolution regardless
    # of the conformal score. A case with an unresolved HIGH/CRITICAL
    # contradiction is definitionally a case a human should see.
    contradiction_block_severities: list[str] = ["HIGH", "CRITICAL"]

    # -- Semantic contradiction layer (arbiter.evidence.nli) --
    # Optional local path or HF repo id for the DeBERTa-v3-MNLI checkpoint.
    # The ENGINE is not configurable -- it is DeBERTa-NLI and nothing else,
    # and no generative model may serve this layer (see nli.py's module
    # docstring). Only WHERE the weights live is configurable.
    nli_model_path: str | None = None


class ConfigurationError(RuntimeError):
    """Raised at startup when settings are unsafe for the target environment."""


def validate_for_environment(settings: "Settings") -> None:
    """Fail closed at boot rather than serve traffic with a known-insecure
    configuration. Called from `arbiter.main`'s lifespan before any router
    handles a request."""
    if settings.env == "dev":
        return

    problems: list[str] = []
    if settings.auth_secret == Settings.model_fields["auth_secret"].default:
        problems.append(
            "ARBITER_AUTH_SECRET is still the built-in development default -- every "
            "bearer token in this deployment is forgeable by anyone who has read the "
            "source. Set a high-entropy secret from a real secret store."
        )
    if settings.enable_dev_auth:
        problems.append(
            "ARBITER_ENABLE_DEV_AUTH is true outside env=dev -- POST /v1/auth/dev-token "
            "mints ADMIN tokens to unauthenticated callers. Disable it and issue tokens "
            "from a real IdP."
        )
    if not settings.signing_key_seed and not settings.signing_key_ring:
        problems.append(
            "Neither ARBITER_SIGNING_KEY_SEED nor ARBITER_SIGNING_KEY_RING is set -- the "
            "audit signing key would be ephemeral and every signature written by this "
            "process becomes unverifiable on restart."
        )
    if problems:
        raise ConfigurationError(
            f"refusing to start with env={settings.env!r}:\n  - " + "\n  - ".join(problems)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
