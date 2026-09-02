"""Application configuration.

Every tunable value - and every secret - is read from the environment (or a
local `.env` file) instead of being hardcoded. `get_settings()` is cached so the
environment is parsed once per process and the same immutable object is shared
by every module that needs it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---------------------------------------------------------
    app_name: str = "AI Project Reviewer"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True

    # --- API -----------------------------------------------------------------
    api_v1_prefix: str = "/api/v1"
    #: `NoDecode` matters more than it looks. Without it pydantic-settings tries
    #: to JSON-decode any complex-typed environment variable *before* a
    #: validator can see it, so the comma-separated form every deployment
    #: platform's UI produces - and the form .env.example documents -
    #: raises SettingsError at import time and the process never starts.
    #: With it the raw string reaches `_split_cors_origins` below.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    # --- GitHub --------------------------------------------------------------
    # Optional. Without a token GitHub allows 60 requests/hour per IP; with one,
    # 5000. The application works on public repositories either way. The token
    # is used server-side only and is never sent to the browser.
    github_token: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_timeout_seconds: int = 20

    # --- Repository retrieval limits -----------------------------------------
    # Guard-rails so a huge repository can never be downloaded blindly. Every
    # limit is configuration, so it can be tuned per environment.
    max_files: int = 40
    #: Unauthenticated GitHub allows only 60 requests/hour per IP, and each
    #: analysis costs roughly `max_files + 4` requests. Without a token we use a
    #: smaller cap so a single analysis cannot exhaust the whole hourly budget.
    max_files_unauthenticated: int = 15
    max_file_size_bytes: int = 100_000        # skip any single file above this
    max_total_content_bytes: int = 600_000    # stop once the budget is spent
    max_tree_entries_returned: int = 400      # paths only, never file contents
    max_concurrent_file_requests: int = 5     # be a polite API client

    # --- Local LLM (Ollama) --------------------------------------------------
    # The model name is configuration, never a literal in the code, so swapping
    # models - or swapping in another local provider - is an env-file change.
    ollama_base_url: str = "http://localhost:11434"
    #: No default that pretends to exist - whatever is set here must actually be
    #: pulled locally (`ollama list`). Never hardcoded anywhere else.
    ollama_model: str = "gemma3:4b"
    #: Local CPU inference is slow. A 4B model on CPU can take minutes for a
    #: full repository prompt, so this is generous by default.
    ollama_timeout_seconds: int = 600
    #: Ollama defaults num_ctx to 4096 regardless of what the model supports, so
    #: we set it explicitly. Raise it only if the machine has the RAM for it.
    ollama_num_ctx: int = 8192
    #: 0 = as deterministic as the runtime allows. Analysis should not be creative.
    ollama_temperature: float = 0.0
    #: One retry with a stricter instruction if the model returns unusable JSON.
    ollama_max_attempts: int = 2
    #: Optional fixed sampling seed. Ollama does not pin a seed by default, so
    #: even at temperature 0 successive runs can differ. Set this to make a run
    #: reproducible. -1 (the default) means "let Ollama choose".
    ollama_seed: int = -1

    # --- Analysis pipeline ----------------------------------------------------
    #: "fast" (default) or "deep".
    #:
    #: Measured on gemma3:4b, 2 CPU cores, psf/requests: the deep pipeline spent
    #: 673 of 677 seconds inside the model, and 375s of that was *prompt*
    #: processing at 16.7 tok/s - because stages 1 and 2 each re-sent nearly the
    #: same 2,800-token context, and stage 3 re-sent their summaries.
    #:
    #: Fast mode sends that context once and asks for one bounded object. It
    #: gives up the model's chance to reason in separate passes; it gives up no
    #: evidence, because every citation is validated identically either way.
    analysis_mode: str = "fast"

    #: Superseded by `analysis_mode`, and honoured only when set explicitly, so
    #: an existing ENABLE_MULTI_STAGE=true keeps selecting the deep pipeline.
    enable_multi_stage: bool = True

    #: Hard ceilings on what the model may write. Constrained decoding enforces
    #: a JSON Schema exactly, so these are not hints - they are the difference
    #: between an answer that ends and one that does not. Before they existed
    #: every array was unbounded, and the findings stage alone generated 678
    #: output tokens at 4.2 tok/s.
    max_findings_per_area: int = 3
    max_evidence_per_finding: int = 2

    # --- LLM context budget ---------------------------------------------------
    # Separate from the GitHub retrieval limits: we may retrieve more than we can
    # afford to put in a prompt. Measured in characters (~4 chars per token).
    #: Measured on gemma3:4b (CPU): ~12k chars costs roughly 5 minutes PER STAGE,
    #: so a three-stage run took 12 minutes. 8k keeps a full run nearer 7-8
    #: minutes. Raise it if you have a faster machine or a GPU.
    max_llm_context_chars: int = 8_000
    max_llm_chars_per_file: int = 2_500

    @field_validator("analysis_mode", mode="before")
    @classmethod
    def _normalise_analysis_mode(cls, value: object) -> object:
        """Accept any casing; reject anything that is not a real mode."""
        if isinstance(value, str):
            mode = value.strip().lower()
            if mode not in ("fast", "deep"):
                raise ValueError(
                    f"ANALYSIS_MODE must be 'fast' or 'deep', not {value!r}."
                )
            return mode
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept `A,B,C` from the environment as well as a real JSON list."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass  # fall through and treat it as a plain list
            return [origin.strip() for origin in text.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _production_must_be_safe(self) -> "Settings":
        """Fail fast rather than serve production traffic misconfigured.

        Each of these is a mistake that is silent until it matters: debug
        logging that quietly records third-party request detail, a CORS list
        that lets any site call the API, or an empty one that makes the API
        unreachable from its own frontend. A refused boot is a loud, cheap
        failure; the alternative is a quiet one nobody notices.
        """
        if not self.is_production:
            return self

        if self.debug:
            raise ValueError(
                "DEBUG must be false when ENVIRONMENT=production. Debug logging "
                "records third-party request detail that does not belong in "
                "production logs."
            )

        if "*" in self.cors_origins:
            raise ValueError(
                "CORS_ORIGINS must not contain '*' in production. List the exact "
                "frontend origins, e.g. https://your-app.vercel.app"
            )

        if not self.cors_origins:
            raise ValueError(
                "CORS_ORIGINS is empty. Set it to the frontend origin(s) that "
                "must be allowed to call this API."
            )

        return self

    @property
    def use_multi_stage(self) -> bool:
        """Whether to run the three-pass pipeline.

        `ENABLE_MULTI_STAGE` predates `ANALYSIS_MODE` and still appears in
        deployments and .env files, so an explicit setting wins. Only an
        explicit one: its default is True, and honouring that would pin every
        install to the slow path the new default exists to avoid.
        """
        if "enable_multi_stage" in self.model_fields_set:
            return self.enable_multi_stage
        return self.analysis_mode == "deep"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def effective_max_files(self) -> int:
        """File cap for this process, lowered when no GitHub token is set."""
        if self.github_token:
            return self.max_files
        return min(self.max_files, self.max_files_unauthenticated)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
