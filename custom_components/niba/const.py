DOMAIN = "niba"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"

API_BASE = "https://api.clientes.niba.es"

UPDATE_INTERVAL = 15 * 60

CONF_INVOICE_ESTIMATE = "invoice_estimate_enabled"
CONF_ENERGY_PUNTA = "energy_punta"
CONF_ENERGY_LLANO = "energy_llano"
CONF_ENERGY_VALLE = "energy_valle"
CONF_POWER_P1_KW = "power_p1_kw"
CONF_POWER_P2_KW = "power_p2_kw"
CONF_POWER_P1_DAY = "power_p1_day"
CONF_POWER_P2_DAY = "power_p2_day"
CONF_SURPLUS_KWH = "surplus_kwh"
CONF_RENTAL_DAY = "rental_day"
CONF_SOCIAL_DAY = "social_day"
CONF_ELECTRICITY_TAX = "electricity_tax_percent"
CONF_VAT = "vat_percent"
CONF_VALLEY_DATES = "valley_dates"

DEFAULT_TARIFF_OPTIONS = {
    CONF_INVOICE_ESTIMATE: False,
    CONF_ENERGY_PUNTA: 0.0,
    CONF_ENERGY_LLANO: 0.0,
    CONF_ENERGY_VALLE: 0.0,
    CONF_POWER_P1_KW: 0.0,
    CONF_POWER_P2_KW: 0.0,
    CONF_POWER_P1_DAY: 0.0,
    CONF_POWER_P2_DAY: 0.0,
    CONF_SURPLUS_KWH: 0.0,
    CONF_RENTAL_DAY: 0.0,
    CONF_SOCIAL_DAY: 0.0,
    CONF_ELECTRICITY_TAX: 0.0,
    CONF_VAT: 0.0,
    CONF_VALLEY_DATES: "",
}
