"""HubSpot (developer test account) — disputes become Tickets on the customer's Company."""
from __future__ import annotations

import logging

from agent.config import get_settings
from agent.integrations.base import IntegrationError

log = logging.getLogger("ops_agent.hubspot")

TICKET_TO_COMPANY_ASSOCIATION = 26  # HubSpot-defined association type id: ticket -> company


class HubSpotClient:
    def __init__(self) -> None:
        s = get_settings()
        self.configured = bool(s.hubspot_access_token)
        self._client = None
        if self.configured:
            from hubspot import HubSpot

            self._client = HubSpot(access_token=s.hubspot_access_token)

    # --- companies ---------------------------------------------------------------------------------------
    def ensure_company(self, name: str, existing_id: str | None) -> str:
        if existing_id:
            return existing_id
        from hubspot.crm.companies import SimplePublicObjectInputForCreate

        try:
            obj = self._client.crm.companies.basic_api.create(
                simple_public_object_input_for_create=SimplePublicObjectInputForCreate(properties={"name": name})
            )
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"hubspot company: {exc}") from exc
        return str(obj.id)

    # --- tickets ------------------------------------------------------------------------------------------
    def create_ticket(self, subject: str, content: str, company_id: str | None, priority: str = "MEDIUM") -> str:
        from hubspot.crm.tickets import SimplePublicObjectInputForCreate

        associations = None
        if company_id:
            associations = [{
                "to": {"id": company_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": TICKET_TO_COMPANY_ASSOCIATION}],
            }]
        props = {"subject": subject[:200], "content": content[:5000], "hs_pipeline": "0", "hs_pipeline_stage": "1",
                 "hs_ticket_priority": priority}
        try:
            obj = self._client.crm.tickets.basic_api.create(
                simple_public_object_input_for_create=SimplePublicObjectInputForCreate(properties=props, associations=associations)
            )
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"hubspot ticket: {exc}") from exc
        return str(obj.id)

    def delete(self, external_id: str) -> None:
        if not self.configured or not external_id:
            return
        try:
            self._client.crm.tickets.basic_api.archive(ticket_id=external_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("hubspot archive %s failed: %s", external_id, exc)

    def ticket_url(self, external_id: str) -> str | None:
        return None if not self.configured else f"https://app.hubspot.com/contacts/tickets/{external_id}"
