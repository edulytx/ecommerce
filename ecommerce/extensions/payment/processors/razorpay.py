""" Razorpay payment processing. """
from __future__ import unicode_literals

import logging
import uuid
from oscar.apps.payment.exceptions import GatewayError, TransactionDeclined


from ecommerce.core.url_utils import get_ecommerce_url
from ecommerce.extensions.payment.processors import BasePaymentProcessor, HandledProcessorResponse
import razorpay
logger = logging.getLogger(__name__)


class Razorpay(BasePaymentProcessor):
    """
    The Razorpay processor Class and it's methods support payment processing via razorpay
    """
    NAME = 'razorpay'
    template_name = 'payment/razorpay.html'

    def __init__(self, site):
        """ Initialize method """
        """
              Constructs a new instance of the Razorpay processor.

              Raises:
                  KeyError: If a required setting is not configured for this payment processor
              """
        super(Razorpay, self).__init__(site)
        configuration = self.configuration

        # Added by edulytX
        self.api_key = configuration['api_key']
        self.api_password = configuration['api_password']
        self.secret_key = configuration['api_password']
        self.razorpay_client = razorpay.Client(auth=(self.api_key, self.api_password))

    @property
    def cancel_page_url(self):
        return get_ecommerce_url(self.configuration['cancel_checkout_path'])

    def _get_basket_amount(self, basket):
        return basket.total_incl_tax * 100

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
        parameters = {
            'amount': self._get_basket_amount(basket),
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

        self.record_processor_response(payment.to_dict(), transaction_id=payment.id, basket=basket)
        logger.info("Successfully created Razorpay payment [%s] for basket [%d].", payment.id, basket.id)
        order_id = payment['id']

        # Add the extra parameters
        parameters.update(kwargs.get('extra_parameters', {}))
        parameters.update(kwargs.get('order_id', order_id))
        return parameters

    def handle_processor_response(self, response, basket=None):
        """
        All the Transaction is done in this method.
        :param response:
        :param basket:
        :return:
        """
        payment_id = response['razorpay_payment_id']
        signature = response['razorpay_signature']
        razorpay_order_id = response['razorpay_order_id']
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



