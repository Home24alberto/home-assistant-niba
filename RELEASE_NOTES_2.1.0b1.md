# Niba Energía 2.1.0b1 — versión de prueba

Calcula el coste de las curvas horarias completas con precios leídos del contrato
y contrastados con la última factura. Separa energía, total acumulado y previsión.
Añade consumo valorado, fecha, días, potencia, compensación, alquiler, bono social
e impuestos. Conserva los identificadores anteriores y las facturas oficiales.

## Compatibilidad y límites

El formato comprobado es Niba Tres, tarifa 2.0TD, Península/Baleares, con factura
de referencia compatible. Los precios son de cada contrato, no están fijados a
un cliente. Zen, Flex, otros formatos, varios suministros y otros territorios
quedan pendientes de soporte. Sin datos suficientes, el cálculo queda sin valor
con diagnóstico; nunca se sustituye por precios de catálogo.

La comprobación con un periodo completo de curvas de un suministro mostró una
diferencia de 0,07 € respecto a la factura, cuyos consumos por franja se presentaban
redondeados. Esto no garantiza la misma precisión en todos los clientes.

Impuestos, alquiler y otros cargos se toman de la última factura como referencia
provisional. El periodo es el mes natural. No se incluyen ajustes de saldo,
servicios adicionales ni descuentos no compatibles. No se exige entrada manual
de precios; sí disponer de documentos compatibles y una factura de referencia.

## Validación y publicación

Pruebas locales de cálculo, cobertura horaria, cambio de hora, documentos,
privacidad y creación de sensores. Las pruebas de Home Assistant usan dobles de
sus interfaces; todavía no se ha probado la instalación en un HA real.
Incluye workflows HACS, Hassfest y pruebas Python. Sus resultados remotos están
pendientes. Publicar como PRE-RELEASE, sin sustituir aún la versión estable.

## Actualización desde 2.0.6

Guardar una copia de custom_components/niba, sustituir la carpeta completa y
reiniciar Home Assistant. No borrar la integración ni volver a crearla. Home
Assistant instala pypdf automáticamente. Revisar los nuevos sensores de total,
consumo valorado, días y fecha antes de adaptar el panel.

Las tarjetas antiguas pueden seguir mostrando cálculos propios. No descontar
excedentes otra vez del nuevo total acumulado: ya están incluidos.

## Privacidad

No se incluyen documentos, credenciales ni identificadores personales.
Los documentos se descargan y procesan en memoria. Los enlaces de descarga no
reciben las credenciales de la API. Los atributos contienen campos seleccionados.
