"""Resolve Vite's hashed assets through Django's existing static storage."""

import json
from pathlib import Path

from django import template
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.templatetags.static import static

register = template.Library()


@register.inclusion_tag("web/frontend_assets.html")
def frontend_assets():
    """Load Vite's development entry or the production manifest's scripts and styles."""
    dev_url = settings.FRONTEND_DEV_SERVER_URL if settings.DEBUG else ""
    if dev_url:
        return {"dev_url": f"{dev_url}/static/frontend"}

    manifest_path = Path(settings.FRONTEND_DIST_DIR) / ".vite" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
        entry = manifest["src/main.tsx"]
    except (OSError, ValueError, KeyError) as exc:
        raise ImproperlyConfigured(
            "Frontend assets are missing. Run `npm ci --prefix frontend` and "
            "`npm run build --prefix frontend`, or set FRONTEND_DEV_SERVER_URL for Vite."
        ) from exc

    styles, imports, seen = [], [], set()

    def collect(chunk):
        """Collect each shared chunk once so its styles and module preload are included."""
        for css in chunk.get("css", []):
            url = static(f"frontend/{css}")
            if url not in styles:
                styles.append(url)
        for key in chunk.get("imports", []):
            if key not in seen:
                seen.add(key)
                imported = manifest[key]
                imports.append(static(f"frontend/{imported['file']}"))
                collect(imported)

    collect(entry)
    return {"script": static(f"frontend/{entry['file']}"), "styles": styles, "imports": imports}
