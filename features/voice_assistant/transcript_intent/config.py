"""Backend settings; API secret is never a desktop configuration field."""
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BackendConfig:
    enabled: bool = False
    free_tier_verified: bool = False
    api_key: str = field(default='', repr=False)
    model: str = 'gemini-3.5-flash-lite'
    project: str = ''
    timeout_seconds: float = 8
    rpm: int = 1
    rpd: int = 10
    daily_reserve: int = 2
    retries: int = 0
    quota_path: str = 'runtime/gemini-intent-quota.json'
    tpm: int = 2400

    def __post_init__(self):
        if self.model != 'gemini-3.5-flash-lite':
            raise ValueError('Only the reviewed free-tier candidate is enabled; verify another model before changing this policy')
        if not 1 <= self.timeout_seconds <= 8 or self.rpm < 1 or self.rpd < 1 or self.tpm < 1200:
            raise ValueError('Invalid timeout/quota cap')
        if not 0 <= self.daily_reserve < self.rpd or self.retries not in (0, 1):
            raise ValueError('Invalid reserve/retry limit')

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv('GEMINI_INTENT_ENABLED') == 'true',
                   free_tier_verified=os.getenv('GEMINI_FREE_TIER_VERIFIED') == 'true',
                   api_key=os.getenv('GEMINI_API_KEY', ''),
                   model=os.getenv('GEMINI_INTENT_MODEL', 'gemini-3.5-flash-lite'),
                   project=os.getenv('GEMINI_PROJECT_ID', ''),
                   timeout_seconds=float(os.getenv('GEMINI_INTENT_TIMEOUT_SECONDS', '8')),
                   rpm=int(os.getenv('GEMINI_INTENT_RPM_CAP', '1')),
                   rpd=int(os.getenv('GEMINI_INTENT_RPD_CAP', '10')),
                   daily_reserve=int(os.getenv('GEMINI_INTENT_DAILY_RESERVE', '2')),
                   retries=int(os.getenv('GEMINI_INTENT_RETRIES', '0')),
                   quota_path=os.getenv('GEMINI_INTENT_QUOTA_PATH', 'runtime/gemini-intent-quota.json'),
                   tpm=int(os.getenv('GEMINI_INTENT_TPM_CAP', '2400')))
