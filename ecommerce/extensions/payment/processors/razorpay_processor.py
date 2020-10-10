""" Razorpay payment processing. """
from __future__ import unicode_literals

import logging
import uuid
import razorpay
import json
from oscar.apps.payment.exceptions import TransactionDeclined


from ecommerce.core.url_utils import get_ecommerce_url
from ecommerce.extensions.payment.processors import BasePaymentProcessor, HandledProcessorResponse
logger = logging.getLogger(__name__)


class RazorpayProcessor(BasePaymentProcessor):
    """
    The Razorpay processor Class and it's methods support payment processing via razorpay
    """
    NAME = 'razorpay'
    #template_name = 'payment/razorpay.html'

    def __init__(self, site):
        """ Initialize method """
        """
              Constructs a new instance of the Razorpay processor.

              Raises:
                  KeyError: If a required setting is not configured for this payment processor
              """
        super(RazorpayProcessor, self).__init__(site)
        configuration = self.configuration

        # Added by edulytX
        self.api_key = configuration['api_key']
        self.api_password = configuration['api_password']
        self.secret_key = configuration['api_password']
        self.razorpay_client = razorpay.Client(auth=(self.api_key, self.api_password))

    @property
    def cancel_url(self):
        return get_ecommerce_url(self.configuration['cancel_checkout_path'])

    @property
    def error_url(self):
        return get_ecommerce_url(self.configuration['error_path'])

    def get_transaction_parameters(self, basket, request=None, use_client_side_checkout=False, **kwargs):
        """
        Generate a dictionary of signed parameters Razorpay requires to complete a transaction.

        Arguments:
            use_client_side_checkout:
            basket (Basket): The basket of products being purchased.
            request (Request, optional): A Request object which could be used to construct an absolute URL; not
                used by this method.
            use_client_side_checkout (bool, optional): Indicates if the Silent Order POST profile should be used.
            **kwargs: Additional parameters.

        Keyword Arguments:
            extra_parameters (dict): Additional signed parameters that should be included in the signature
                and returned dict. Note that these parameters will override any default values.

        Returns:
            dict: Razorpay-specific parameters required to complete a transaction.
        """

        parameters = self._generate_parameters(basket, **kwargs)
        return parameters

    def _generate_parameters(self, basket, **kwargs):
        """ Generates the parameters dict.

        A signature is NOT included in the parameters.

         Arguments:
            basket (Basket): Basket from which the pricing and item details are pulled.
            **kwargs: Additional parameters to add to the generated dict.

         Returns:
             dict: Dictionary containing the payment parameters that should be sent to CyberSource.
        """
        amount = basket.total_incl_tax
        parameters = {
            'amount': int(amount*100),
            'currency': 'INR',
            'receipt': uuid.uuid4().hex,
            'payment_capture': 1
        }
        payment = None
        # Generate order id
        try:
            payment = self.razorpay_client.order.create(data=parameters)
        except razorpay.errors.BadRequestError:
            error = 'API credentials provided are invalid.'
            entry = self.record_processor_response(
                {'error': error},
                transaction_id=basket.id,
                basket=basket
            )
            logger.error(
                u"%s [%d], %s [%d].",
                "Failed to create PayPal payment for basket",
                basket.id,
                "PayPal's response recorded in entry",
                entry.id,
                exc_info=True
            )
            raise TransactionDeclined(error, basket.id, 500)

        payment_id = payment['id']
        self.record_processor_response(payment, transaction_id=payment_id, basket=basket)
        logger.info("Successfully created Razorpay payment [%s] for basket [%d].", payment_id, basket.id)

        # Add the extra parameters
        parameters.update(kwargs.get('extra_parameters', {}))
        basket_owner = basket.owner
        if basket_owner is not None:
            parameters['user'] = basket_owner.username
        parameters['invoice_number']  = basket.order_number
        parameters['basket_id'] = basket.id
        parameters['payment_id'] = payment_id
        parameters['amount'] = int(basket.total_incl_tax)
        items_list =  [
                        {
                            'quantity': line.quantity,
                            'name': line.product.title,
                            'price': unicode(line.line_price_incl_tax_incl_discounts / line.quantity)
                        }
                        for line in basket.all_lines()
                    ]

        parameters['items_list'] = json.dumps(items_list)
        parameters['razorpay_api_key'] = self.api_key
        parameters['payment_page_url']  = "/payment/razorpay/form/" + str(basket.id) + "/"

        return parameters

    def handle_processor_response(self, response, basket=None):
        """
        All the Transaction is done in this method.
        :param response:
        :param basket:
        :return:
        """
        payment_id = response.get('razorpay_payment_id')
        signature = response.get('razorpay_signature')
        razorpay_order_id = response.get('razorpay_order_id')
        try:
            param_dict = {'razorpay_order_id': razorpay_order_id, 'razorpay_payment_id': payment_id,
                          'razorpay_signature': signature}
            self.razorpay_client.utility.verify_payment_signature(param_dict)

        except razorpay.errors.SignatureVerificationError as ex:
            msg = 'Verification of Signature for Razorpay payment for basket [%d] failed with HTTP status [%d]'
            body = ex.json_body

            logger.exception(msg + ': %s', basket.id, ex.http_status, body)
            self.record_processor_response(body, basket=basket)
            raise TransactionDeclined(msg, basket.id, ex.http_status)

        total = basket.total_incl_tax
        currency = basket.currency
        email = basket.owner
        label = 'Razorpay ({})'.format(email) if email else 'Razorpay Account'

        return HandledProcessorResponse(
            transaction_id=payment_id,
            total=total,
            currency=currency,
            card_number=label,
            card_type=None
        )

    def issue_credit(self, order_number, basket, reference_number, amount, currency):
        raise NotImplementedError('The Razorpay payment processor does not implement issue_credit method.')


