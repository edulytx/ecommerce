import logging

from oscar.core.loading import get_class, get_model
from ecommerce.extensions.basket.utils import basket_add_organization_attribute
from ecommerce.extensions.checkout.mixins import EdxOrderPlacementMixin
from ecommerce.extensions.checkout.utils import get_receipt_page_url
from ecommerce.extensions.payment.forms import RazorpaySubmitForm
from ecommerce.extensions.payment.processors.razorpay_processor import RazorpayProcessor
from ecommerce.extensions.payment.views import BasePaymentSubmitView
from django.views.generic import View
from django.shortcuts import redirect
from django.db import transaction
from django.utils.decorators import method_decorator
import json
from django.shortcuts import render
from django.core.exceptions import MultipleObjectsReturned
from oscar.apps.partner import strategy
from oscar.apps.payment.exceptions import PaymentError



logger = logging.getLogger(__name__)

Applicator = get_class('offer.applicator', 'Applicator')
BillingAddress = get_model('order', 'BillingAddress')
Country = get_model('address', 'Country')
NoShippingRequired = get_class('shipping.methods', 'NoShippingRequired')
OrderTotalCalculator = get_class('checkout.calculators', 'OrderTotalCalculator')
PaymentProcessorResponse = get_model('payment', 'PaymentProcessorResponse')


class RazorpayPaymentFormView(View):
    """
        Displays the Razor payment pay form.
    """
    def post(self, request, basket_id, *args, **kwargs):
        return self.__show_razorpay_form(request, basket_id)

    def __show_razorpay_form(self, request, basket_id):
        """
        Displays the Razorpay form to complete the payment.
        :param request:
        :param basket_id:
        :return:
        """
        payment_id = request.POST.get('payment_id')
        amount = request.POST.get('amount')
        user = request.POST.get('user')
        razorpay_api_key = request.POST.get('razorpay_api_key')
        if user is None:
            user = 'Unknown'
        invoice_number = request.POST.get('invoice_number')
        if invoice_number is None:
            invoice_number = 'Unknown'

        items_list  = request.POST.get('items_list')
        if items_list is None:
            items_list = {}
        else:
            items_list = json.loads(items_list)

        return render(request, 'payment/razorpay.html', {
            'amount': amount,
            'payment_id': payment_id,
            'user': user,
            'invoice_number': invoice_number,
            'items_list': items_list,
            'razorpay_api_key': razorpay_api_key,
            'basket_id': basket_id,
            'amount_paid': int(amount)/100
        })


class RazorpaySubmitView(EdxOrderPlacementMixin, View):
    """ Stripe payment handler.

    The payment form should POST here. This view will handle creating the charge at Stripe, creating an order,
    and redirecting the user to the receipt page.
    """
    form_class = RazorpaySubmitForm
    @property
    def payment_processor(self):
        return RazorpayProcessor(self.request.site)

    # Disable atomicity for the view. Otherwise, we'd be unable to commit to the database
    # until the request had concluded; Django will refuse to commit when an atomic() block
    # is active, since that would break atomicity. Without an order present in the database
    # at the time fulfillment is attempted, asynchronous order fulfillment tasks will fail.
    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request, *args, **kwargs):
        return super(RazorpaySubmitView, self).dispatch(request, *args, **kwargs)

    def _get_basket(self, payment_id):
        """
        Retrieve a basket using a payment ID.

        Arguments:
            payment_id: payment_id received from Razorpay.

        Returns:
            It will return related basket or log exception and return None if
            duplicate payment_id received or any other exception occurred.

        """
        try:
            basket = PaymentProcessorResponse.objects.get(
                processor_name=self.payment_processor.NAME,
                transaction_id=payment_id
            ).basket
            basket.strategy = strategy.Default()
            Applicator().apply(basket, basket.owner, self.request)

            basket_add_organization_attribute(basket, self.request.GET)
            return basket
        except MultipleObjectsReturned:
            logger.warning(u"Duplicate payment ID [%s] received from PayPal.", payment_id)
            return None
        except Exception:  # pylint: disable=broad-except
            logger.exception(u"Unexpected error during basket retrieval while executing PayPal payment.")
            return None

    def post(self, request):
        razorpay_response = request.POST.dict()
        print(razorpay_response)
        payment_id = request.POST.get('payment_id')
        basket = self._get_basket(payment_id)

        if not basket:
            return redirect(self.payment_processor.error_url)

        receipt_url = get_receipt_page_url(
            order_number=basket.order_number,
            site_configuration=basket.site.siteconfiguration
        )

        try:
            with transaction.atomic():
                try:
                    self.handle_payment(razorpay_response, basket)
                except PaymentError:
                    return redirect(self.payment_processor.error_url)
        except:  # pylint: disable=bare-except
            logger.exception('Attempts to handle payment for basket [%d] failed.', basket.id)
            return redirect(receipt_url)

        self.call_handle_order_placement(basket, request)

        return redirect(receipt_url)

    def call_handle_order_placement(self, basket, request):
        try:
            shipping_method = NoShippingRequired()
            shipping_charge = shipping_method.calculate(basket)
            order_total = OrderTotalCalculator().calculate(basket, shipping_charge)

            user = basket.owner
            # Given a basket, order number generation is idempotent. Although we've already
            # generated this order number once before, it's faster to generate it again
            # than to retrieve an invoice number from PayPal.
            order_number = basket.order_number

            order = self.handle_order_placement(
                order_number=order_number,
                user=user,
                basket=basket,
                shipping_address=None,
                shipping_method=shipping_method,
                shipping_charge=shipping_charge,
                billing_address=None,
                order_total=order_total,
                request=request
            )
            self.handle_post_order(order)

        except Exception:  # pylint: disable=broad-except
            self.log_order_placement_exception(basket.order_number, basket.id)
