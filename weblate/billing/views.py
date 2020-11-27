#
# Copyright © 2012 - 2020 Michal Čihař <michal@cihar.com>
#
# This file is part of Weblate <https://weblate.org/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from weblate.billing.models import Billing, Invoice


@login_required
def download_invoice(request, pk):
    """Download invoice PDF."""
    invoice = get_object_or_404(Invoice, pk=pk)

    if not invoice.ref:
        raise Http404("No reference!")

    if not request.user.has_perm("billing.view", invoice.billing):
        raise PermissionDenied()

    if not invoice.filename_valid:
        raise Http404(f"File {invoice.filename} does not exist!")

    with open(invoice.full_filename, "rb") as handle:
        data = handle.read()

    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f"attachment; filename={invoice.filename}"
    response["Content-Length"] = len(data)

    return response


def handle_post(request, billing):
    def get(name):
        try:
            return int(request.POST[name])
        except (KeyError, ValueError):
            return None

    recurring = get("recurring")
    terminate = get("terminate")
    if not recurring and not terminate:
        return
    if recurring:
        if "recurring" in billing.payment:
            del billing.payment["recurring"]
        billing.save()
    elif terminate:
        billing.state = Billing.STATE_TERMINATED
        billing.save()


@login_required
def overview(request):
    billings = Billing.objects.for_user(request.user).prefetch_related(
        "plan", "projects", "invoice_set"
    )
    if len(billings) == 1:
        return redirect(billings[0])
    return render(
        request,
        "billing/overview.html",
        {
            "billings": billings,
            "active_billing_count": billings.filter(
                state__in=(Billing.STATE_ACTIVE, Billing.STATE_TRIAL)
            ).count(),
        },
    )


@login_required
def detail(request, pk):
    billing = get_object_or_404(Billing, pk=pk)

    if not request.user.has_perm("billing.view", billing):
        raise PermissionDenied()

    if request.method == "POST":
        handle_post(request, billing)
        return redirect(billing)

    return render(
        request,
        "billing/detail.html",
        {
            "billing": billing,
        },
    )
