"""Admin panel. Run with `python -m alitrends.panel` (see README for Tailscale setup)."""
from .app import create_app

__all__ = ["create_app"]
