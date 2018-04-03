import logging

from lxml import etree

from pretix import __version__ as pversion
from . import __version__


logger = logging.getLogger('pretix_sofort')


class SofortError(Exception):
    pass


class MultiPay:
    def __init__(self, project_id, amount, currency_code, reasons, user_variables, success_url=None,
                 success_link_redirect=1, abort_url=None, timeout_url=None, notification_urls=None,
                 beneficiary_identifier=None, beneficiary_country_code=None):
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
        root = etree.fromstring(xml)
        if root.tag == 'errors':
            raise SofortError(xml)
        return cls(
            transaction=root.xpath('/new_transaction/transaction')[0].text,
            payment_url=root.xpath('/new_transaction/payment_url')[0].text,
        )

