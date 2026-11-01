# Niba Energía 2.1.1 — Historial persistente de ciclos

- Guarda localmente las curvas recibidas, los precios usados y el último cálculo válido. El historial sobrevive a reinicios y cambios de mes.
- Conserva el cálculo original del cierre e intenta completar por separado las lecturas pendientes del último ciclo cerrado.
- Añade cinco entidades: ciclos guardados, último cierre calculado, último cierre facturado, diferencia y estado.
- Compara únicamente una factura con fechas exactamente coincidentes y un cálculo con cobertura completa. Las diferencias son orientativas y pueden incluir ajustes o conceptos adicionales.
- El primer día del ciclo muestra 0 € acumulados cuando contrato y tarifa están validados: significa cero días completos contabilizados. No convierte errores ni ausencia de curvas de días posteriores en cero.
- Mantiene los identificadores de las entidades existentes.

## Actualización
Actualiza desde HACS y reinicia Home Assistant. No borres ni vuelvas a crear la integración. Busca «Niba Ciclos guardados» para comprobar el almacenamiento tras la primera actualización correcta.

El historial empieza con el ciclo actual al instalar esta versión; no recupera automáticamente ciclos anteriores. Antes del primer cierre es normal que sus entidades estén sin datos. El panel no se modifica automáticamente.

## Validación y límites
39 pruebas automatizadas con los límites de Home Assistant simulados. No probado contra una instalación ni una API real. El cálculo no sustituye a la factura oficial. Los ciclos completados no vuelven a consultar revisiones posteriores de curvas. El historial no tiene purga automática y debe incluirse en las copias de seguridad.
