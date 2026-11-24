"""The public run workspace shares Django's validation and execution services."""

import pytest
from django.urls import reverse

from factory.notebooks.models import NotebookRun

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frontend_assets")]


def test_run_route_requires_session(client, template):
    response = client.get(reverse("web:build-run", args=[template.slug]))
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_run_configuration_filters_levels_and_preserves_area(client, user, template, nepal):
    client.force_login(user)
    template.area_levels = [1]
    template.save()
    response = client.get(reverse("web:build-run", args=[template.slug]), {"area": nepal["a1"].pk})
    assert response.status_code == 200
    config = response.context["frontend_config"]["run"]
    assert config["countries"] == [{"iso3": "NPL", "name": "Nepal", "levels": [1]}]
    assert config["country"] == "NPL"
    assert config["level"] == 1
    assert config["areaIds"] == str(nepal["a1"].pk)
    assert 'name="threshold"' in config["parametersHtml"]
    assert "no-store" in response["Cache-Control"]


def test_run_country_prefill_and_invalid_post_stay_in_react(client, user, template, nepal):
    client.force_login(user)
    url = reverse("web:build-run", args=[template.slug])
    assert client.get(url, {"country": "npl"}).context["frontend_config"]["run"]["country"] == "NPL"
    response = client.post(
        url,
        {
            "country": "NPL",
            "level": 1,
            "area_ids": nepal["a1"].pk,
            "threshold": "2",
            "hazard": "flood",
            "notify_emails": "not-an-email",
        },
    )
    assert response.status_code == 200
    config = response.context["frontend_config"]["run"]
    assert config["errors"]
    assert config["optionsOpen"]
    assert 'aria-invalid="true"' in config["optionsHtml"]
    assert config["areaIds"] == str(nepal["a1"].pk)
    assert 'value="2.0"' in config["parametersHtml"]
    assert not NotebookRun.objects.exists()


@pytest.mark.parametrize("multiple", [False, True])
def test_public_run_post_creates_expected_runs(client, user, template, nepal, multiple):
    client.force_login(user)
    ids = [nepal["a1"].pk, nepal["a2"].pk] if multiple else [nepal["root"].pk]
    response = client.post(
        reverse("web:build-run", args=[template.slug]),
        {
            "country": "NPL",
            "level": 1 if multiple else 0,
            "area_ids": ",".join(map(str, ids)),
            "hazard": "flood",
            "threshold": "0.5",
        },
    )
    assert response.status_code == 302
    assert set(NotebookRun.objects.values_list("area_id", flat=True)) == set(ids)
    assert ("/batches/" if multiple else "/runs/") in response.url


def test_run_field_html_escapes_parameter_values(client, user, template, nepal):
    client.force_login(user)
    template.parameter_schema = {
        "type": "object",
        "properties": {"label": {"type": "string", "default": '<script>alert("bad")</script>'}},
    }
    template.save()
    config = client.get(reverse("web:build-run", args=[template.slug])).context["frontend_config"]["run"]
    assert "<script>" not in config["parametersHtml"]
    assert "&lt;script&gt;" in config["parametersHtml"]


@pytest.mark.parametrize("codes,expected", [(["NPL"], "NPL"), (["NPL", "IND"], "")])
def test_event_country_prefill_does_not_guess_for_multi_country_events(
    client, user, template, nepal, montandon, codes, expected
):
    from factory.events.models import StacItem

    client.force_login(user)
    item = StacItem.objects.create(
        catalog=montandon, collection="events", item_id="event-1", country_codes=codes
    )
    config = client.get(reverse("web:build-run", args=[template.slug]), {"stac_item": item.pk}).context[
        "frontend_config"
    ]["run"]
    assert config["country"] == expected
    assert config["stacItem"] == str(item.pk)
    response = client.post(
        reverse("web:build-run", args=[template.slug]), {"stac_item": item.pk, "threshold": 2}
    )
    assert response.context["frontend_config"]["run"]["stacItem"] == str(item.pk)


def test_public_run_requires_csrf(client, user, template, nepal):
    from django.test import Client

    browser = Client(enforce_csrf_checks=True)
    browser.force_login(user)
    url = reverse("web:build-run", args=[template.slug])
    assert browser.post(url, {}).status_code == 403
    config = browser.get(url).context["frontend_config"]
    response = browser.post(url, {"csrfmiddlewaretoken": config["csrfToken"]})
    assert response.status_code == 200
    assert response.context["frontend_config"]["run"]["errors"]


def test_public_run_hides_inactive_templates_from_regular_users(client, user, template):
    client.force_login(user)
    template.is_active = False
    template.save()
    assert client.get(reverse("web:build-run", args=[template.slug])).status_code == 404


def test_template_details_link_to_public_configuration(client, template):
    response = client.get(template.get_absolute_url(), {"country": "NPL"})
    assert (reverse("web:build-run", args=[template.slug]) + "?country=NPL").encode() in response.content


@pytest.mark.parametrize("route", ["web:build-run", "web:run-new"])
def test_prefilled_event_can_run_without_refetching(
    client, user, item_template, nepal, montandon, sample_item, monkeypatch, route
):
    """Both forms reuse a stored event even when its catalog is temporarily unavailable."""
    from factory.events.matching import upsert_item
    from factory.events.stac_client import StacClientError

    item, *_ = upsert_item(montandon, sample_item)
    client.force_login(user)
    url = reverse(route, args=[item_template.slug])

    def unavailable_catalog(*args, **kwargs):
        raise StacClientError("Catalog unavailable")

    def unexpected_fetch(*args, **kwargs):
        pytest.fail("An unchanged stored event must not be fetched again")

    monkeypatch.setattr("factory.web.stac_picker.catalog_collections", unavailable_catalog)
    monkeypatch.setattr("factory.web.forms.fetch_item", unexpected_fetch)
    response = client.get(url, {"stac_item": item.pk})
    assert response.status_code == 200
    if route == "web:build-run":
        config = response.context["frontend_config"]["run"]
        assert config["stacItem"] == str(item.pk)
        assert "Catalog unavailable" in config["stacHtml"]
    else:
        assert f'name="stac_item" value="{item.pk}"'.encode() in response.content

    response = client.post(
        url,
        {
            "country": "NPL",
            "level": 0,
            "area_ids": nepal["root"].pk,
            "radius": 10,
            "stac_item": item.pk,
            "stac_catalog": montandon.pk,
            "stac_collection": item.collection,
            "stac_item_id": item.item_id,
        },
    )
    assert response.status_code == 302
    assert NotebookRun.objects.get().stac_item == item


@pytest.mark.parametrize("route", ["web:build-run", "web:run-new"])
def test_area_optional_templates_do_not_need_boundaries(client, user, template, route):
    """The new workspace preserves the original ability to queue an analysis without an area."""
    client.force_login(user)
    template.requires_area = False
    template.save()
    response = client.post(
        reverse(route, args=[template.slug]),
        {"hazard": "flood", "threshold": "0.5"},
    )
    assert response.status_code == 302
    assert NotebookRun.objects.get().area is None
