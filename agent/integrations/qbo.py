"""QuickBooks Online (sandbox) — approved supplier invoices become Bills against the Vendor."""
from __future__ import annotations

import logging
from decimal import Decimal

from agent.config import get_settings
from agent.integrations.base import IntegrationError

log = logging.getLogger("ops_agent.qbo")


class QBOClient:
    def __init__(self) -> None:
        s = get_settings()
        self.configured = bool(s.qbo_client_id and s.qbo_client_secret and s.qbo_refresh_token and s.qbo_realm_id)
        self._qb = None
        self._expense_account_ref = None
        if self.configured:
            from intuitlib.client import AuthClient
            from quickbooks import QuickBooks

            auth = AuthClient(client_id=s.qbo_client_id, client_secret=s.qbo_client_secret,
                              redirect_uri="https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl",
                              environment=s.qbo_environment)
            self._qb = QuickBooks(auth_client=auth, refresh_token=s.qbo_refresh_token, company_id=s.qbo_realm_id)

    def _expense_account(self):
        if self._expense_account_ref is None:
            from quickbooks.objects.account import Account

            accounts = Account.filter(AccountType="Expense", qb=self._qb)
            if not accounts:
                raise IntegrationError("qbo: no Expense account in company")
            self._expense_account_ref = accounts[0].to_ref()
        return self._expense_account_ref

    def ensure_vendor(self, name: str, existing_id: str | None) -> str:
        from quickbooks.objects.vendor import Vendor

        if existing_id:
            return existing_id
        try:
            found = Vendor.filter(DisplayName=name, qb=self._qb)
            if found:
                return str(found[0].Id)
            v = Vendor()
            v.DisplayName = name
            v.save(qb=self._qb)
            return str(v.Id)
        except IntegrationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"qbo vendor: {exc}") from exc

    def create_bill(self, vendor_id: str, vendor_name: str, amount: Decimal, invoice_number: str | None,
                    due_date: str | None, memo: str) -> str:
        from quickbooks.objects.base import Ref
        from quickbooks.objects.bill import Bill
        from quickbooks.objects.detailline import AccountBasedExpenseLine, AccountBasedExpenseLineDetail

        try:
            bill = Bill()
            ref = Ref()
            ref.type, ref.value, ref.name = "Vendor", vendor_id, vendor_name
            bill.VendorRef = ref
            if invoice_number:
                bill.DocNumber = invoice_number[:21]
            if due_date and len(due_date) == 10 and due_date[4] == "-":
                bill.DueDate = due_date
            bill.PrivateNote = memo[:4000]
            line = AccountBasedExpenseLine()
            line.Amount = float(amount)
            line.DetailType = "AccountBasedExpenseLineDetail"
            line.Description = memo[:4000]
            line.AccountBasedExpenseLineDetail = AccountBasedExpenseLineDetail()
            line.AccountBasedExpenseLineDetail.AccountRef = self._expense_account()
            bill.Line.append(line)
            bill.save(qb=self._qb)
            return str(bill.Id)
        except IntegrationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise IntegrationError(f"qbo bill: {exc}") from exc

    def delete(self, external_id: str) -> None:
        if not self.configured or not external_id:
            return
        from quickbooks.objects.bill import Bill

        try:
            Bill.get(external_id, qb=self._qb).delete(qb=self._qb)
        except Exception as exc:  # noqa: BLE001
            log.warning("qbo delete bill %s failed: %s", external_id, exc)

    def bill_url(self, external_id: str) -> str | None:
        if not self.configured:
            return None
        host = "sandbox.qbo.intuit.com" if get_settings().qbo_environment == "sandbox" else "qbo.intuit.com"
        return f"https://{host}/app/bill?txnId={external_id}"
