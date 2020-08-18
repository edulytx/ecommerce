import logging

from django.http import JsonResponse
from oscar.core.loading import get_class, get_model

from ecommerce.extensions.basket.utils import basket_add_organization_attribute
from ecommerce.extensions.checkout.mixins import EdxOrderPlacementMixin
from ecommerce.extensions.checkout.utils import get_receipt_page_url
from ecommerce.extensions.payment.forms import RazorpaySubmitForm
from ecommerce.extensions.payment.processors.razorpay import Razorpay
from ecommerce.extensions.payment.views import BasePaymentSubmitView

logger = logging.getLogger(__name__)

Applicator = get_class('offer.applicator', 'Applicator')
BillingAddress = get_model('order', 'BillingAddress')
Country = get_model('address', 'Country')
NoShippingRequired = get_class('shipping.methods', 'NoShippingRequired')
OrderTotalCalculator = get_class('checkout.calculators', 'OrderTotalCalculator')


class RazorpaySubmitView(EdxOrderPlacementMixin, BasePaymentSubmitView):
    """ Stripe payment handler.

    The payment form should POST here. This view will handle creating the charge at Stripe, creating an order,
    and redirecting the user to the receipt page.
    """
    form_class = RazorpaySubmitForm

    @property
    def payment_processor(self):
        return Razorpay(self.request.site)

    def form_valid(self, form):
        form_data = form.cleaned_data
        basket = form_data['basket']
        order_number = basket.order_number

        basket_add_organization_attribute(basket, self.request.POST)

        try:
            self.handle_payment(form_data, basket)
        except Exception:  # pylint: disable=broad-except
            logger.exception('An error occurred while processing the Stripe payment for basket [%d].', basket.id)
            return JsonResponse({}, status=400)

        shipping_method = NoShippingRequired()
        shipping_charge = shipping_method.calculate(basket)
        order_total = OrderTotalCalculator().calculate(basket, shipping_charge)

        order = self.handle_order_placement(
            order_number=order_number,
            user=basket.owner,
            basket=basket,
            shipping_address=None,
            shipping_method=shipping_method,
            shipping_charge=shipping_charge,
            billing_address=None,
            order_total=order_total,
            request=self.request
        )
        self.handle_post_order(order)

        receipt_url = get_receipt_page_url(
            site_configuration=self.request.site.siteconfiguration,
            order_number=order_number
        )
        return JsonResponse({'url': receipt_url}, status=201)
