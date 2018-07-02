# coding=utf-8
from itertools import chain

from flask import render_template, request, redirect, url_for, abort, session, Markup, flash
from flask_login import current_user, current_app

from dmapiclient import APIError
from dmapiclient.audit import AuditTypes
from dmcontent.content_loader import ContentNotFoundError
from dmutils.dates import update_framework_with_formatted_dates
from dmutils.email import send_user_account_email
from dmutils.email.dm_mailchimp import DMMailChimpClient
from dmutils.forms import remove_csrf_token, get_errors_from_wtform

from ...main import main, content_loader
from ... import data_api_client
from ..forms.suppliers import (
    AddCompanyRegisteredNameForm,
    AddCompanyRegistrationNumberForm,
    CompanyOrganisationSizeForm,
    CompanyPublicContactInformationForm,
    CompanyTradingStatusForm,
    DunsNumberForm,
    EditContactInformationForm,
    EditRegisteredAddressForm,
    EditRegisteredCountryForm,
    EditSupplierForm,
    EmailAddressForm,
    VatNumberForm,
)
from ..helpers.frameworks import (
    get_frameworks_by_status,
    get_frameworks_closed_and_open_for_applications,
)
from ..helpers.suppliers import (
    COUNTRY_TUPLE,
    get_country_name_from_country_code,
    supplier_company_details_are_complete,
)
from ..helpers import login_required

JOIN_OPEN_FRAMEWORK_NOTIFICATION_MAILING_LIST_SUCCESS_MESSAGE = (
    "You will receive email notifications to {email_address} when applications are opening."
)
JOIN_OPEN_FRAMEWORK_NOTIFICATION_MAILING_LIST_ERROR_MESSAGE = Markup("""
    The service is unavailable at the moment. If the problem continues please contact
    <a href="mailto:enquiries@digitalmarketplace.service.gov.uk">enquiries@digitalmarketplace.service.gov.uk</a>
""")


@main.route('')
@login_required
def dashboard():
    supplier = data_api_client.get_supplier(
        current_user.supplier_id
    )['suppliers']
    supplier['contact'] = supplier['contactInformation'][0]

    all_frameworks = sorted(
        data_api_client.find_frameworks()['frameworks'],
        key=lambda framework: framework['slug'],
        reverse=True
    )
    supplier_frameworks = {
        framework['frameworkSlug']: framework
        for framework in data_api_client.get_supplier_frameworks(current_user.supplier_id)['frameworkInterest']
    }

    for framework in all_frameworks:
        framework.update(
            supplier_frameworks.get(framework['slug'], {})
        )

        update_framework_with_formatted_dates(framework)
        framework.update({
            'deadline': Markup(f"Deadline: {framework['applicationsCloseAt']}"),
            'registered_interest': (framework['slug'] in supplier_frameworks),
            'made_application': (
                framework.get('declaration')
                and framework['declaration'].get('status') == 'complete'
                and framework.get('complete_drafts_count') > 0
            ),
            'needs_to_complete_declaration': (
                framework.get('onFramework') and framework.get('agreementReturned') is False
            )
        })

    if "currently_applying_to" in session:
        del session["currently_applying_to"]

    return render_template(
        "suppliers/dashboard.html",
        supplier=supplier,
        frameworks={
            'coming': get_frameworks_by_status(all_frameworks, 'coming'),
            'open': get_frameworks_by_status(all_frameworks, 'open'),
            'pending': get_frameworks_by_status(all_frameworks, 'pending'),
            'standstill': get_frameworks_by_status(all_frameworks, 'standstill', 'made_application'),
            'live': get_frameworks_by_status(all_frameworks, 'live', 'services_count')
        }
    ), 200


@main.route('/details', methods=['GET'])
@login_required
def supplier_details():
    try:
        supplier = data_api_client.get_supplier(
            current_user.supplier_id
        )['suppliers']
    except APIError as e:
        abort(e.status_code)
    supplier['contact'] = supplier['contactInformation'][0]
    country_name = get_country_name_from_country_code(supplier.get('registrationCountry'))

    framework = None

    if "currently_applying_to" in session:
        framework = data_api_client.get_framework(session["currently_applying_to"])["frameworks"]
    return render_template(
        "suppliers/details.html",
        supplier=supplier,
        country_name=country_name,
        currently_applying_to=framework,
        company_details_complete=supplier_company_details_are_complete(supplier),
        supplier_company_details_confirmed=supplier['companyDetailsConfirmed'],
    ), 200


@main.route('/details', methods=['POST'])
@login_required
def confirm_supplier_details():
    try:
        supplier = data_api_client.get_supplier(
            current_user.supplier_id
        )['suppliers']
    except APIError as e:
        abort(e.status_code)

    if not supplier_company_details_are_complete(supplier):
        abort(400, "Some company details are not complete")
    else:
        try:
            data_api_client.update_supplier(
                supplier_id=current_user.supplier_id,
                supplier={"companyDetailsConfirmed": True},
                user=current_user.email_address
            )
        except APIError as e:
            abort(e.status_code)

    if "currently_applying_to" in session:
        return redirect(url_for(".framework_dashboard", framework_slug=session["currently_applying_to"]))
    else:
        return redirect(url_for(".supplier_details"))


@main.route('/registered-address/edit', methods=['GET', 'POST'])
@login_required
def edit_registered_address():
    try:
        supplier = data_api_client.get_supplier(
            current_user.supplier_id
        )['suppliers']
    except APIError as e:
        abort(e.status_code)
    supplier['contact'] = supplier['contactInformation'][0]

    registered_address_form = EditRegisteredAddressForm()
    registered_country_form = EditRegisteredCountryForm()

    if request.method == 'POST':
        address_valid = registered_address_form.validate_on_submit()
        country_valid = registered_country_form.validate_on_submit()

        if address_valid and country_valid:
            try:
                data_api_client.update_supplier(
                    current_user.supplier_id,
                    remove_csrf_token(registered_country_form.data),
                    current_user.email_address,
                )

                data_api_client.update_contact_information(
                    current_user.supplier_id,
                    supplier['contact']['id'],
                    remove_csrf_token(registered_address_form.data),
                    current_user.email_address
                )

            except APIError as e:
                abort(e.status_code)

            return redirect(url_for(".supplier_details"))

    else:
        registered_address_form.address1.data = supplier['contact'].get('address1')
        registered_address_form.city.data = supplier['contact'].get('city')
        registered_address_form.postcode.data = supplier['contact'].get('postcode')

        registered_country_form.registrationCountry.data = supplier.get('registrationCountry')

    errors = {**get_errors_from_wtform(registered_address_form), **get_errors_from_wtform(registered_country_form)}

    return render_template(
        "suppliers/registered_address.html",
        supplier=supplier,
        countries=COUNTRY_TUPLE,
        registered_address_form=registered_address_form,
        registered_country_form=registered_country_form,
        errors=errors,
    ), 200 if not errors else 400


@main.route('/registered-company-name/edit', methods=['GET', 'POST'])
@login_required
def edit_supplier_registered_name():
    form = AddCompanyRegisteredNameForm()
    supplier = data_api_client.get_supplier(current_user.supplier_id)['suppliers']
    if supplier.get("registeredName") and supplier.get('companyDetailsConfirmed'):
        return (
            render_template(
                "suppliers/already_completed.html",
                completed_data_description="registered company name",
                company_details_change_email=current_app.config['DM_COMPANY_DETAILS_CHANGE_EMAIL'],
            ), 200 if request.method == 'GET' else 400
        )

    if request.method == 'POST':
        if form.validate_on_submit():
            try:
                data_api_client.update_supplier(supplier_id=current_user.supplier_id,
                                                supplier={"registeredName": form.registered_company_name.data},
                                                user=current_user.email_address)
                return redirect(url_for('.supplier_details'))
            except APIError as e:
                abort(e.status_code)
        else:
            current_app.logger.warning(
                "supplieredit.fail: registered-name:{rname}, errors:{rname_errors}",
                extra={
                    'rname': form.registered_company_name.data,
                    'rname_errors': ",".join(form.registered_company_name.errors)
                })

    else:
        form.registered_company_name.data = supplier.get("registeredName")

    errors = get_errors_from_wtform(form)

    return render_template(
        'suppliers/edit_registered_name.html',
        form=form,
        errors=errors,
        company_details_change_email=current_app.config['DM_COMPANY_DETAILS_CHANGE_EMAIL']
    ), 200 if not errors else 400


@main.route('/registration-number/edit', methods=['GET', 'POST'])
@login_required
def edit_supplier_registration_number():
    form = AddCompanyRegistrationNumberForm()
    supplier = data_api_client.get_supplier(current_user.supplier_id)['suppliers']
    if (supplier.get("companiesHouseNumber") or supplier.get("otherCompanyRegistrationNumber")) \
            and supplier.get('companyDetailsConfirmed'):
        return (
            render_template(
                "suppliers/already_completed.html",
                completed_data_description="registration number",
                company_details_change_email=current_app.config['DM_COMPANY_DETAILS_CHANGE_EMAIL'],
            ),
            200 if request.method == 'GET' else 400
        )

    if request.method == 'POST':
        if form.validate_on_submit():
            try:
                if form.has_companies_house_number.data == "Yes":
                    data_api_client.update_supplier(
                        supplier_id=current_user.supplier_id,
                        supplier={"companiesHouseNumber": form.companies_house_number.data.upper(),
                                  "otherCompanyRegistrationNumber": None},
                        user=current_user.email_address
                    )
                else:
                    data_api_client.update_supplier(
                        supplier_id=current_user.supplier_id,
                        supplier={"companiesHouseNumber": None,
                                  "otherCompanyRegistrationNumber": form.other_company_registration_number.data},
                        user=current_user.email_address
                    )
                return redirect(url_for('.supplier_details'))
            except APIError as e:
                abort(e.status_code)
        else:
            current_app.logger.warning(
                "supplieredit.fail: has-companies-house-number:{hasnum}, companies-house-number:{chnum}, "
                "other-registered-company-number:{rnumber}, errors:{rnumber_errors}",
                extra={
                    'hasnum': form.has_companies_house_number.data,
                    'chnum': form.companies_house_number.data,
                    'rnumber': form.other_company_registration_number.data,
                    'rnumber_errors': ",".join(form.errors)
                })

    else:
        if supplier.get('companiesHouseNumber'):
            form.has_companies_house_number.data = "Yes"
            form.companies_house_number.data = supplier.get('companiesHouseNumber')

        elif supplier.get('otherCompanyRegistrationNumber'):
            form.has_companies_house_number.data = "No"
            form.other_company_registration_number.data = supplier.get('otherCompanyRegistrationNumber')

    errors = get_errors_from_wtform(form)

    return render_template(
        'suppliers/edit_company_registration_number.html',
        form=form,
        errors=errors,
    ), 200 if not errors else 400


@main.route('/edit', methods=['GET'])
@login_required
def edit_what_buyers_will_see_redirect():
    # redirect old route for this view
    return redirect(url_for('.edit_what_buyers_will_see'), 302)


@main.route('/what-buyers-will-see/edit', methods=['GET', 'POST'])
@login_required
def edit_what_buyers_will_see():
    try:
        supplier = data_api_client.get_supplier(
            current_user.supplier_id
        )['suppliers']
    except APIError as e:
        abort(e.status_code)

    supplier['contact'] = supplier['contactInformation'][0]

    supplier_form = EditSupplierForm()
    contact_form = EditContactInformationForm()

    if request.method == 'POST':
        supplier_info_valid = supplier_form.validate_on_submit()
        contact_info_valid = contact_form.validate_on_submit()

        if supplier_info_valid and contact_info_valid:
            try:
                data_api_client.update_supplier(
                    current_user.supplier_id,
                    remove_csrf_token(supplier_form.data),
                    current_user.email_address
                )

                data_api_client.update_contact_information(
                    current_user.supplier_id,
                    supplier['contact']['id'],
                    remove_csrf_token(contact_form.data),
                    current_user.email_address
                )
            except APIError as e:
                abort(e.status_code)
            else:
                return redirect(url_for(".supplier_details"))

    else:
        supplier_form.description.data = supplier.get('description', None)
        contact_form.contactName.data = supplier['contact'].get('contactName')
        contact_form.phoneNumber.data = supplier['contact'].get('phoneNumber')
        contact_form.email.data = supplier['contact'].get('email')

    errors = {**get_errors_from_wtform(contact_form), **get_errors_from_wtform(supplier_form)}

    return render_template(
        "suppliers/edit_what_buyers_will_see.html",
        supplier_form=supplier_form,
        contact_form=contact_form,
        errors=errors,
    ), 200 if not errors else 400


@main.route('/organisation-size/edit', methods=['GET', 'POST'])
@login_required
def edit_supplier_organisation_size():
    form = CompanyOrganisationSizeForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            try:
                data_api_client.update_supplier(supplier_id=current_user.supplier_id,
                                                supplier={"organisationSize": form.organisation_size.data},
                                                user=current_user.email_address)

            except APIError as e:
                abort(e.status_code)

            return redirect(url_for('.supplier_details'))

        current_app.logger.warning(
            "supplieredit.fail: organisation-size:{osize}, errors:{osize_errors}",
            extra={
                'osize': form.organisation_size.data,
                'osize_errors': ",".join(form.organisation_size.errors)
            })

    supplier = data_api_client.get_supplier(current_user.supplier_id)['suppliers']
    form.organisation_size.data = supplier.get('organisationSize', None)

    errors = get_errors_from_wtform(form)

    return render_template(
        'suppliers/edit_supplier_organisation_size.html',
        form=form,
        errors=errors
    ), 200 if not errors else 400


@main.route('/trading-status/edit', methods=['GET', 'POST'])
@login_required
def edit_supplier_trading_status():
    form = CompanyTradingStatusForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            try:
                data_api_client.update_supplier(supplier_id=current_user.supplier_id,
                                                supplier={"tradingStatus": form.trading_status.data},
                                                user=current_user.email_address)

            except APIError as e:
                abort(e.status_code)

            return redirect(url_for('.supplier_details'))

        current_app.logger.warning(
            "supplieredit.fail: trading-status:{tstatus}, errors:{tstatus_errors}",
            extra={
                'tstatus': form.trading_status.data,
                'tstatus_errors': ",".join(form.trading_status.errors)
            })

    else:
        supplier = data_api_client.get_supplier(current_user.supplier_id)['suppliers']

        prefill_trading_status = None
        if supplier.get('tradingStatus'):
            if supplier['tradingStatus'] in map(lambda x: x['value'], form.OPTIONS):
                prefill_trading_status = supplier['tradingStatus']

        form.trading_status.data = prefill_trading_status

    errors = get_errors_from_wtform(form)

    return render_template(
        'suppliers/edit_supplier_trading_status.html',
        form=form,
        errors=errors
    ), 200 if not errors else 400


@main.route('/vat-number/edit', methods=['GET', 'POST'])
@login_required
def edit_supplier_vat_number():
    form = VatNumberForm()
    try:
        supplier = data_api_client.get_supplier(current_user.supplier_id)['suppliers']
    except APIError as e:
        abort(e.status_code)

    if supplier.get("vatNumber") and supplier.get('companyDetailsConfirmed'):
        return (
            render_template("suppliers/already_completed.html",
                            completed_data_description="VAT number",
                            company_details_change_email=current_app.config['DM_COMPANY_DETAILS_CHANGE_EMAIL'],),
            200 if request.method == 'GET' else 400
        )

    if request.method == 'POST':
        if form.validate_on_submit():
            vat_number = form.vat_number.data if form.vat_registered.data == 'Yes' else form.NOT_VAT_REGISTERED_TEXT

            try:
                data_api_client.update_supplier(supplier_id=current_user.supplier_id,
                                                supplier={"vatNumber": vat_number},
                                                user=current_user.email_address)
            except APIError as e:
                abort(e.status_code)

            return redirect(url_for('.supplier_details'))

        current_app.logger.warning(
            "supplieredit.fail: vat-number:{vat_number}, vat-number-errors:{vat_number_errors}, "
            "vat-registered:{vat_registered}, vat-registered-errors{vat_registered_errors}",
            extra={
                "vat_number": form.vat_number.data,
                "vat_number_errors": ",".join(form.vat_number.errors),
                "vat_registered": form.vat_registered.data,
                "vat_registered_errors": ",".join(form.vat_registered.errors),
            })

    else:
        if supplier.get('vatNumber'):
            if supplier.get('vatNumber') == form.NOT_VAT_REGISTERED_TEXT:
                form.vat_registered.data = 'No'
            else:
                form.vat_registered.data = 'Yes'
                form.vat_number.data = supplier.get('vatNumber')

    errors = get_errors_from_wtform(form)

    return render_template(
        'suppliers/edit_vat_number.html',
        form=form,
        errors=errors
    ), 200 if not errors else 400


@main.route('/duns-number/edit', methods=['GET', 'POST'])
def edit_supplier_duns_number():
    return (
        render_template("suppliers/already_completed.html",
                        completed_data_description="DUNS number",
                        company_details_change_email=current_app.config['DM_COMPANY_DETAILS_CHANGE_EMAIL'],),
        200 if request.method == 'GET' else 400
    )


@main.route('/supply', methods=['GET'])
def become_a_supplier():

    try:
        frameworks = sorted(
            data_api_client.find_frameworks().get('frameworks'),
            key=lambda framework: framework['id'],
            reverse=True
        )
        displayed_frameworks = get_frameworks_closed_and_open_for_applications(frameworks)
        for fwk in displayed_frameworks:
            content_loader.load_messages(fwk.get('slug'), ['become-a-supplier'])

    #  if no message file is found (should never happen), die
    except ContentNotFoundError:
        current_app.logger.error(
            "contentloader.fail No 'become-a-supplier' message file found for framework."
        )
        abort(500)

    return render_template(
        "suppliers/become_a_supplier.html",
        open_fwks=[fwk for fwk in displayed_frameworks if fwk["status"] == "open"],
        opening_fwks=[fwk for fwk in displayed_frameworks if fwk["status"] == "coming"],
        closed_fwks=[fwk for fwk in displayed_frameworks if fwk["status"] not in ("open", "coming")],
        content_loader=content_loader
    ), 200


# Redirect added 23rd March 2018 - can probably remove it in 6 months or so
@main.route('/create', methods=['GET'])
def redirect_to_create_new_supplier():
    return redirect(url_for(".create_new_supplier")), 301


@main.route('/create/start', methods=['GET'])
def create_new_supplier():
    return render_template(
        "suppliers/create_new_supplier.html"
    ), 200


@main.route('/create/duns-number', methods=['GET', 'POST'])
def duns_number():
    form = DunsNumberForm()

    if request.method == 'POST':
        if form.validate_on_submit():
            suppliers = data_api_client.find_suppliers(duns_number=form.duns_number.data)
            if len(suppliers["suppliers"]) <= 0:
                session[form.duns_number.name] = form.duns_number.data
                return redirect(url_for(".company_details"))

            form.duns_number.errors = ["DUNS number already used"]

        current_app.logger.warning(
            "suppliercreate.fail: duns:{duns} {duns_errors}",
            extra={
                'duns': form.duns_number.data,
                'duns_errors': ",".join(form.duns_number.errors)})

    else:
        if form.duns_number.name in session:
            form.duns_number.data = session[form.duns_number.name]

    errors = get_errors_from_wtform(form)

    return render_template(
        "suppliers/create_duns_number.html",
        form=form,
        errors=errors,
    ), 200 if not errors else 400


@main.route('/create/company-details', methods=['GET', 'POST'])
def company_details():
    form = CompanyPublicContactInformationForm()

    if request.method == "POST":
        if form.validate_on_submit():
            session[form.company_name.name] = form.company_name.data
            session[form.email_address.name] = form.email_address.data
            session[form.phone_number.name] = form.phone_number.data
            session[form.contact_name.name] = form.contact_name.data
            return redirect(url_for(".create_your_account"))
        else:
            current_app.logger.warning(
                "suppliercreate.fail: duns:{duns} {form_errors}",
                extra={
                    'duns': session.get('duns_number'),
                    'form_errors': ",".join(chain.from_iterable(form.errors.values()))})

    else:
        if form.company_name.name in session:
            form.company_name.data = session[form.company_name.name]

        if form.contact_name.name in session:
            form.contact_name.data = session[form.contact_name.name]

        if form.email_address.name in session:
            form.email_address.data = session[form.email_address.name]

        if form.phone_number.name in session:
            form.phone_number.data = session[form.phone_number.name]

    errors = get_errors_from_wtform(form)

    return render_template(
        "suppliers/create_company_details.html",
        form=form,
        errors=errors,
    ), 200 if not errors else 400


@main.route('/create/account', methods=['GET', 'POST'])
def create_your_account():
    current_app.logger.info("suppliercreate: {} create-your-account supplier_id:{}".format(
        request.method,
        session.get('email_supplier_id', 'unknown'))
    )

    form = EmailAddressForm()
    if form.validate_on_submit():
        session['account_email_address'] = form.email_address.data
        return redirect(url_for(".company_summary"))

    errors = get_errors_from_wtform(form)

    return render_template(
        "suppliers/create_your_account.html",
        form=form,
        errors=errors,
        email_address=form.email_address.data if form.email_address.data else session.get('account_email_address', '')
    ), 200 if not errors else 400


@main.route('/create/company-summary', methods=['GET'])
def company_summary():
    return render_template(
        "suppliers/create_company_summary.html"
    ), 200


@main.route('/create/company-summary', methods=['POST'])
def submit_company_summary():

    required_fields = [
        "email_address",
        "phone_number",
        "contact_name",
        "duns_number",
        "company_name",
        "account_email_address"
    ]

    missing_fields = [field for field in required_fields if field not in session]

    if not missing_fields:
        supplier = {
            "name": session["company_name"],
            "dunsNumber": str(session["duns_number"]),
            "contactInformation": [{
                "email": session["email_address"],
                "phoneNumber": session["phone_number"],
                "contactName": session["contact_name"]
            }]
        }

        account_email_address = session.get("account_email_address", None)

        supplier = data_api_client.create_supplier(supplier)
        session.clear()
        session['email_company_name'] = supplier['suppliers']['name']
        session['email_supplier_id'] = supplier['suppliers']['id']

        send_user_account_email(
            'supplier',
            account_email_address,
            current_app.config['NOTIFY_TEMPLATES']['create_user_account'],
            extra_token_data={
                "supplier_id": session['email_supplier_id'],
                "supplier_name": session['email_company_name']
            }
        )

        data_api_client.create_audit_event(
            audit_type=AuditTypes.invite_user,
            object_type='suppliers',
            object_id=session['email_supplier_id'],
            data={'invitedEmail': account_email_address})

        return redirect(url_for('.create_your_account_complete'), 302)
    else:
        return render_template(
            "suppliers/create_company_summary.html",
            missing_fields=missing_fields
        ), 400


@main.route('/create/complete', methods=['GET'])
def create_your_account_complete():
    if 'email_sent_to' in session:
        email_address = session['email_sent_to']
    else:
        email_address = "the email address you supplied"
    session.clear()
    session['email_sent_to'] = email_address
    return render_template(
        "suppliers/create_your_account_complete.html",
        email_address=email_address
    ), 200


@main.route('/mailing-list', methods=["GET", "POST"])
def join_open_framework_notification_mailing_list():
    status = 200

    form = EmailAddressForm()
    if form.validate_on_submit():
        dmmc_client = DMMailChimpClient(
            current_app.config["DM_MAILCHIMP_USERNAME"],
            current_app.config["DM_MAILCHIMP_API_KEY"],
            current_app.logger,
        )

        mc_response = dmmc_client.subscribe_new_email_to_list(
            current_app.config["DM_MAILCHIMP_OPEN_FRAMEWORK_NOTIFICATION_MAILING_LIST_ID"],
            form.data["email_address"],
        )

        if mc_response not in (True, False,):
            # success
            data_api_client.create_audit_event(
                audit_type=AuditTypes.mailing_list_subscription,
                data={
                    "subscribedEmail": form.data["email_address"],
                    "mailchimp": {k: mc_response.get(k) for k in (
                        "id",
                        "unique_email_id",
                        "timestamp_opt",
                        "last_changed",
                        "list_id",
                    )},
                },
            )

            flash(JOIN_OPEN_FRAMEWORK_NOTIFICATION_MAILING_LIST_SUCCESS_MESSAGE.format(
                email_address=form.data["email_address"],
            ), "success")

            return redirect("/")
        else:
            # failure
            flash(JOIN_OPEN_FRAMEWORK_NOTIFICATION_MAILING_LIST_ERROR_MESSAGE, "error")
            if mc_response:
                # this is a case where we think the error is *probably* the user's fault in some way
                status = 400
            else:
                # this is a case where we have no idea so should probably be alert to it
                status = 503
            # fall through to re-display form with error
    else:
        status = 400
        # fall through to re-display form with errors

    return render_template(
        "suppliers/join_open_framework_notification_mailing_list.html",
        form=form,
        errors=get_errors_from_wtform(form),
    ), status
