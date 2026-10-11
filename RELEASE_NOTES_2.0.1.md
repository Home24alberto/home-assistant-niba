## Niba Energía v2.0.1

### Correcciones

- El periodo actual usa el mes natural completo con fechas inclusivas.
- Octubre muestra correctamente 31 días.
- Se descartan curvas antiguas, duplicadas y el día todavía abierto.
- Consumo, excedentes, días con datos y proyección utilizan la misma base.
- Se conserva la estimación original de la API como entidad de diagnóstico.

### Nueva previsión completa de factura

- Añadida una previsión opcional que incluye energía, potencia, compensación de
  excedentes, alquiler, financiación del bono social, impuesto eléctrico e IVA.
- Cada usuario introduce sus propios precios y potencias desde **Configurar** en
  la integración.
- El repositorio no contiene tarifas, potencias ni datos personales de ningún
  cliente.
- La previsión permanece desactivada hasta completar la configuración.

La cifra es provisional y no incluye wallet, servicios adicionales ni ajustes
extraordinarios. Después de actualizar, reinicia Home Assistant y revisa las
opciones de Niba Energía.
