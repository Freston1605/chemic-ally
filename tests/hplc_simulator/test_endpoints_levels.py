"""API endpoint coverage for the read-only level routes (R006, S03/T01).

Routes under test:
- ``GET /hplc/api/levels/``          (``api-levels``)
- ``GET /hplc/api/levels/<slug>/``   (``api-level-detail``)

All requests go through ``rest_framework.test.APIClient`` and
``django.urls.reverse`` -- no hardcoded URL strings -- so the tests track the
frozen URLconf instead of a copied path.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.fixture
def client():
    return APIClient()


def _level_list_url():
    return reverse('hplc_simulator:api-levels')


def _level_detail_url(slug):
    return reverse('hplc_simulator:api-level-detail', kwargs={'slug': slug})


class TestLevelListEndpoint:
    """``GET /hplc/api/levels/`` returns active levels only, unpaginated."""

    def test_returns_bare_unpaginated_list_of_active_levels(
        self, client, api_level, api_inactive_level
    ):
        response = client.get(_level_list_url())

        assert response.status_code == 200
        assert response.status_code != 429

        body = response.json()
        # Plain APIView, not a ListAPIView: the payload is a bare list, so the
        # global PageNumberPagination setting must not apply here.
        assert isinstance(body, list)

        slugs = [item['slug'] for item in body]
        assert 'api-level' in slugs
        assert 'api-inactive-level' not in slugs

        item = next(row for row in body if row['slug'] == 'api-level')
        for field in (
            'id',
            'name',
            'slug',
            'difficulty',
            'analyte_count',
            'max_pressure_bar',
        ):
            assert field in item

    def test_analyte_count_reflects_attached_analytes(
        self, client, api_level_with_analytes
    ):
        response = client.get(_level_list_url())

        assert response.status_code == 200
        assert response.status_code != 429

        item = next(
            row
            for row in response.json()
            if row['slug'] == 'api-level-simulate'
        )
        assert item['analyte_count'] == 3


class TestLevelDetailEndpoint:
    """``GET /hplc/api/levels/<slug>/`` returns analytes and constraints."""

    def test_returns_analytes_and_constraints(
        self, client, api_level_with_analytes
    ):
        response = client.get(_level_detail_url('api-level-simulate'))

        assert response.status_code == 200
        assert response.status_code != 429

        body = response.json()
        assert len(body['analytes']) == 3

        constraints = body['constraints']
        for field in (
            'available_columns',
            'max_pressure_bar',
            'solvent_a',
            'solvent_b',
        ):
            assert field in constraints
        assert constraints['available_columns'] == ['C18']
        assert constraints['max_pressure_bar'] == pytest.approx(400.0)

    def test_unknown_slug_returns_404(self, client, api_level):
        response = client.get(_level_detail_url('does-not-exist'))

        assert response.status_code == 404
        assert response.status_code != 429

    def test_inactive_level_returns_404(self, client, api_inactive_level):
        """The ``is_active=True`` filter must hide inactive levels."""
        response = client.get(_level_detail_url('api-inactive-level'))

        assert response.status_code == 404
        assert response.status_code != 429
