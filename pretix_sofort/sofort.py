import json
import logging

from lxml import etree
from lxml.etree import XMLSyntaxError

from pretix import __version__ as pversion

from . import __version__

logger = logging.getLogger('pretix_sofort')


class SofortError(Exception):
    def __init__(self, xml):
        errors = etree.fromstring(xml)
        strl = []
        for e in errors:
            strl.append(e.xpath('message')[0].text)
        self.message = ', '.join(strl)

    def __str__(self):
        return self.message


class MultiPay:
    def __init__(self, project_id, amount, currency_code, reasons, user_variables, success_url=None,
                 success_link_redirect=1, abort_url=None, timeout_url=None, notification_urls=None,
                 beneficiary_identifier=None, beneficiary_country_code=None, timeout=3600):
        self.project_id = project_id
        self.amount = amount
        self.currency_code = currency_code
        self.reasons = reasons
        self.user_variables = user_variables
        self.success_url = success_url
        self.success_link_redirect = success_link_redirect
        self.abort_url = abort_url
        self.timeout_url = timeout_url
        self.notification_urls = notification_urls
        self.beneficiary_identifier = beneficiary_identifier
        self.beneficiary_country_code = beneficiary_country_code
        self.timeout = timeout

    def to_xml(self):
        root = etree.Element('multipay')

        el = etree.Element('project_id')
        el.text = str(self.project_id)
        root.append(el)

        el = etree.Element('interface_version')
        el.text = 'pretix_{}/Sofort_{}'.format(pversion, __version__)
        root.append(el)

        el = etree.Element('amount')
        el.text = str(self.amount)
        root.append(el)

        el = etree.Element('timeout')
        el.text = str(self.timeout)
        root.append(el)

        el = etree.Element('currency_code')
        el.text = self.currency_code
        root.append(el)

        if self.reasons:
            el = etree.Element('reasons')
            for r in self.reasons:
                rel = etree.Element('reason')
                rel.text = str(r)
                el.append(rel)
            root.append(el)

        el = etree.Element('user_variables')
        for r in self.user_variables:
            rel = etree.Element('user_variables')
            rel.text = str(r)
            el.append(rel)
        root.append(el)

        if self.success_url:
            el = etree.Element('success_url')
            el.text = self.success_url
            root.append(el)

        el = etree.Element('success_link_redirect')
        el.text = str(self.success_link_redirect)
        root.append(el)

        if self.abort_url:
            el = etree.Element('abort_url')
            el.text = self.abort_url
            root.append(el)

        if self.notification_urls:
            el = etree.Element('notification_urls')
            for r in self.notification_urls:
                rel = etree.Element('notification_url')
                rel.text = str(r)
                rel.set('notify_on', 'received,loss,refunded,pending')
                el.append(rel)
            root.append(el)

        root.append(etree.Element('su'))

        if self.beneficiary_identifier and self.beneficiary_country_code:
            el = etree.Element('beneficiary')
            rel = etree.Element('identifier')
            rel.text = self.beneficiary_identifier
            el.append(rel)
            rel = etree.Element('country_code')
            rel.text = self.beneficiary_country_code
            el.append(rel)
            root.append(el)

        xml = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + etree.tostring(root, pretty_print=True)
        logger.debug('Generated XML: ' + xml.decode())
        return xml


class NewTransaction:
    def __init__(self, transaction, payment_url):
        self.transaction = transaction
        self.payment_url = payment_url

    @classmethod
    def from_xml(cls, xml):
        try:
            root = etree.fromstring(xml)
        except XMLSyntaxError:
            raise SofortError("Invalid XML received: " + xml.decode())
        if root.tag == 'errors':
            raise SofortError(xml)
        return cls(
            transaction=root.xpath('/new_transaction/transaction')[0].text,
            payment_url=root.xpath('/new_transaction/payment_url')[0].text,
        )


class TransactionRequest:
    def __init__(self, transactions):
        self.transactions = transactions

    def to_xml(self):
        root = etree.Element('transaction_request')
        root.set('version', '2')

        for t in self.transactions:
            el = etree.Element('transaction')
            el.text = str(t)
            root.append(el)

        xml = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + etree.tostring(root, pretty_print=True)
        logger.debug('Generated XML: ' + xml.decode())
        return xml


class TransactionDetails:
    SIMPLE_FIELDS = (
        'project_id', 'transaction', 'test', 'time', 'status', 'status_reason', 'status_modified',
        'payment_method', 'language_code', 'amount', 'amount_refunded', 'currency_code', 'email_customer',
        'phone_customer', 'exchange_rate'
    )
    MORE_FIELDS = ('reasons', 'user_variables', 'sender', 'recipient', 'costs')

    def to_data(self, no_sepa_data=False):
        d = {
            t: getattr(self, t) for t in self.SIMPLE_FIELDS
        }
        d.update({
            t: getattr(self, t) for t in self.MORE_FIELDS
        })
        if no_sepa_data:
            if 'sender' in d['recipient']:
                del d['sender']['account_number']
            if d['sender'].get('iban'):
                d['sender']['iban'] = (
                    d['sender']['iban'][:4]  + ('*' * (len(d['sender']['iban']) - 8)) + d['sender']['iban'][-4:]
                )
            if 'account_number' in d['recipient']:
                del d['recipient']['account_number']
            if d['recipient'].get('iban'):
                d['recipient']['iban'] = (
                    d['recipient']['iban'][:4]  + ('*' * (len(d['recipient']['iban']) - 8)) + d['recipient']['iban'][-4:]
                )
        return d

    def to_json(self, no_sepa_data=False):
        return json.dumps(self.to_data(no_sepa_data))


class Transactions:
    def __init__(self, details):
        self.details = details

    @classmethod
    def from_xml(cls, xml):
        try:
            root = etree.fromstring(xml)
        except XMLSyntaxError:
            raise SofortError("Invalid XML received: " + xml.decode())
        if root.tag == 'errors':
            raise SofortError(xml)

        tdos = []
        for td in root.xpath('/transactions/transaction_details'):
            tdo = TransactionDetails()
            for f in TransactionDetails.SIMPLE_FIELDS:
                setattr(tdo, f, td.xpath('{}'.format(f))[0].text)

            tdo.reasons = [
                r.text for r in td.xpath('reasons/reason')
            ]
            tdo.user_variables = [
                r.text for r in td.xpath('user_variables/user_variable')
            ]
            tdo.sender = {
                r.tag: r.text for r in td.xpath('sender')[0]
            }
            tdo.recipient = {
                r.tag: r.text for r in td.xpath('recipient')[0]
            }
            tdo.costs = {
                r.tag: r.text for r in td.xpath('costs')[0]
            }
            tdos.append(tdo)

        return cls(details=tdos)


class StatusNotification:
    def __init__(self, transaction, time):
        self.transaction = transaction
        self.time = time

    @classmethod
    def from_xml(cls, xml):
        try:
            root = etree.fromstring(xml)
        except XMLSyntaxError:
            raise SofortError("Invalid XML received: " + xml.decode())
        if root.tag == 'errors':
            raise SofortError(xml)

        return cls(
            transaction=root.xpath('/status_notification/transaction')[0].text,
            time=root.xpath('/status_notification/time')[0].text,
        )


class Refund:
    def __init__(self, transaction, amount, comment, reason_1, reason_2, status='created'):
        self.transaction = transaction
        self.amount = amount
        self.comment = comment
        self.reason_1 = reason_1
        self.reason_2 = reason_2
        self.status = status


class Refunds:
    def __init__(self, refunds):
        self.refunds = refunds

    def to_xml(self):
        root = etree.Element('refunds')
        root.set('version', '3')

        for t in self.refunds:
            el = etree.Element('refund')

            rel = etree.Element('transaction')
            rel.text = t.transaction
            el.append(rel)

            rel = etree.Element('amount')
            rel.text = str(t.amount)
            el.append(rel)

            rel = etree.Element('comment')
            rel.text = t.comment
            el.append(rel)

            rel = etree.Element('reason_1')
            rel.text = t.reason_1
            el.append(rel)

            rel = etree.Element('reason_2')
            rel.text = t.reason_2
            el.append(rel)

            root.append(el)

        xml = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + etree.tostring(root, pretty_print=True)
        logger.debug('Generated XML: ' + xml.decode())
        return xml

    @classmethod
    def from_xml(cls, xml):
        try:
            root = etree.fromstring(xml)
        except XMLSyntaxError:
            raise SofortError("Invalid XML received: " + xml.decode())
        if root.tag == 'errors':
            raise SofortError(xml)

        tdos = []
        for td in root.xpath('/refunds/refund'):
            kwargs = {}
            for f in ('transaction', 'amount', 'comment', 'reason_1', 'reason_2', 'status'):
                kwargs[f] = td.xpath('{}'.format(f))[0].text
            if kwargs.get('status') == 'error':
                raise SofortError(etree.tostring(td.xpath('errors')[0]))
            tdo = Refund(**kwargs)
            tdos.append(tdo)

        return cls(tdos)
