"""Read the verified Niba Tres contract table without relying on line positions.

This extracts reference prices only. It does not establish contract validity,
current taxes, or support for other plans or document layouts.
"""
import re
import unicodedata
from decimal import Decimal


class ContractTableError(ValueError):
    pass


def read_contract_table(text):
    text = unicodedata.normalize('NFKD', text.casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = ' '.join(text.split())
    parts = text.split('tus precios')
    if len(parts) != 2:
        raise ContractTableError('tabla_no_unica')
    table = re.split(r'a estos precios', parts[1], maxsplit=1)[0]
    if not re.search(r'potencia\s*\(\s*€/kw\s+dia\s*\)', table) or not re.search(r'energia\s*\(\s*€/kwh\s*\)', table):
        raise ContractTableError('unidades_no_reconocidas')
    heading = re.compile(r'\b(potencia|energia)\s+(punta|llano|valle)\s*\(p([123])\)')
    matches = list(heading.finditer(table))
    expected = {('potencia','punta','1'), ('potencia','valle','2'),
                ('energia','punta','1'), ('energia','llano','2'), ('energia','valle','3')}
    if len(matches) != 5 or {m.groups() for m in matches} != expected:
        raise ContractTableError('periodos_no_reconocidos')
    result = {}
    for index, match in enumerate(matches):
        stop = matches[index+1].start() if index+1 < len(matches) else len(table)
        block = table[match.end():stop]
        values = re.findall(r'(?<![\w.,])-?\d+[.,]\d+(?![\w.,])', block)
        if len(values) != 1:
            raise ContractTableError('precio_ausente_o_ambiguo')
        price = Decimal(values[0].replace(',', '.'))
        if price < 0:
            raise ContractTableError('precio_negativo_no_compatible')
        kind, band, _ = match.groups()
        result[kind + '_' + band] = str(price)
    return result


def read_contract_pdf(content):
    """Read agreeing price tables across pages; reject conflicting versions."""
    from io import BytesIO
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted or len(reader.pages) > 20:
            raise ContractTableError('contrato_pdf_no_compatible')
        tables = []
        for page in reader.pages:
            text = page.extract_text() or ''
            normalized = ' '.join(text.casefold().split())
            if 'tus precios' in normalized:
                tables.append(read_contract_table(text))
        return agree_tables(tables)
    except ContractTableError:
        raise
    except Exception:
        raise ContractTableError('contrato_pdf_no_legible') from None


def agree_tables(tables):
    if not tables:
        raise ContractTableError('contrato_sin_tabla_precios')
    if any(table != tables[0] for table in tables[1:]):
        raise ContractTableError('contrato_con_precios_contradictorios')
    return dict(tables[0])


def reconcile_contract_invoice(prices, tariff):
    """Use signed rates only when the latest invoice confirms the same rates.

    Other charges remain invoice references, not guaranteed future amounts.
    Never silently prefer a stale contract over a more recent invoice.
    """
    for key, value in prices.items():
        if key not in tariff or Decimal(value) != Decimal(str(tariff[key])):
            raise ContractTableError('precios_contrato_factura_distintos')
    return {**tariff, **prices}
