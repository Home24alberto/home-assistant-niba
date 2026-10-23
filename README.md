# Niba Energía para Home Assistant

Integración comunitaria de Home Assistant para consultar los datos disponibles
en una cuenta de Niba Energía. No es oficial ni está afiliada a Niba. La API
utilizada no es pública y puede cambiar sin previo aviso.

## Funciones

- Configuración desde **Ajustes → Dispositivos y servicios**.
- Actualización periódica de contrato, consumo, curvas, facturas y otros datos
  que Niba ofrezca para la cuenta.
- Reautenticación desde Home Assistant cuando caducan las credenciales.
- Entidades de diagnóstico para distinguir el resumen del periodo y las curvas
  diarias.

La disponibilidad de cada dato depende de Niba y de la cuenta. La integración
no genera lecturas que la API no haya publicado.

## Instalación

### Desde HACS — recomendado

Niba Energía está incluida en el catálogo de HACS.

1. Abre **HACS** en Home Assistant.
2. Busca **Niba Energía**.
3. Abre la integración y descárgala.
4. Reinicia Home Assistant.
5. Ve a **Ajustes → Dispositivos y servicios → Añadir integración**.
6. Busca **Niba Energía**.
7. Introduce el correo electrónico y la contraseña de tu cuenta de Niba.

No es necesario introducir los precios de tu tarifa manualmente.

### Alternativa: repositorio personalizado

Si Niba Energía todavía no aparece en la búsqueda de HACS:

1. Abre **HACS → ⋮ → Repositorios personalizados**.
2. Añade esta dirección:
   https://github.com/Home24alberto/home-assistant-niba
3. Selecciona la categoría **Integración**.
4. Añade el repositorio y descarga **Niba Energía**.
5. Reinicia Home Assistant y configura la integración siguiendo los pasos anteriores.

## Versión 2.1.0

Publicación estable. El cálculo de factura es orientativo y su compatibilidad
depende de que se puedan validar los precios y documentos del contrato.
El contraste con un mes completo de curvas sigue pendiente; no se anuncia
compatibilidad con todas las tarifas.

## Consumo y coste del periodo actual

El cálculo usa las curvas horarias publicadas y los precios del documento del
contrato, contrastados con la última factura. No usa precios del catálogo
comercial. El lector requiere una tabla reconocible y precios inequívocos.
Si contrato y factura difieren, no elige un precio silenciosamente: indica el
motivo y deja el cálculo monetario sin valor.

El total acumulado incluye energía, potencia, compensación de excedentes,
alquiler, financiación del bono social, impuesto eléctrico e IVA. Los cargos
adicionales e impuestos son referencias de la última factura, cuya continuidad
se supone provisionalmente. No se garantiza que sigan vigentes ante cambios
contractuales, fiscales o de condiciones.

Solo se valoran días consecutivos completos desde el comienzo del mes. El
consumo valorado, el coste y la fecha de cobertura figuran juntos en los nuevos
sensores. Si faltan horas o sus sumas no coinciden con el total diario, se
interrumpe la valoración; no se rellenan huecos con cero. Los sensores antiguos
de consumo pueden incluir otras curvas disponibles y muestran su propia fecha.

La previsión al cierre es independiente del acumulado. Se estima con las
observaciones disponibles por tipo de día y solo cuando hay muestra suficiente.
La factura oficial sigue siendo el importe definitivo. El periodo calculado es
el mes natural; no representa automáticamente ciclos de facturación distintos.

### Alcance comprobado

- Lector de tabla Niba Tres con tres precios de energía y dos de potencia.
- Horarios de Península y Baleares, con control de cambios de hora.
- Compensación fija cuando coincide con la factura de referencia.
- Validación aritmética del desglose de la factura y pruebas unitarias.

No se han validado formatos Zen, Flex/indexados, Ceuta, Melilla ni Canarias.
Tampoco se calculan facturas con ajustes de saldo, descuentos u otros servicios
no representados por el modelo. Se requiere una factura compatible de referencia;
una cuenta nueva sin ella podrá mostrar consumos, pero no este total estimado.
Con varios suministros activos la selección automática no está disponible.

### Actualización y paneles existentes

Los identificadores de las entidades existentes se conservan. Los sensores de
coste de energía pasan a usar el cálculo horario; los importes del resumen de
Niba quedan como diagnóstico, sin atribuirles un desglose que no está verificado.
Los nuevos sensores de total acumulado y previsión incluyen la compensación:
no debe descontarse de nuevo en una tarjeta. Revisa las plantillas antes de
sustituir las tarjetas antiguas. El panel no se modifica automáticamente.

Los documentos se procesan en memoria; no se publican PDFs, enlaces firmados
ni datos personales en los atributos. Los precios se mantienen en memoria y
se recuperan de nuevo después de reiniciar. Home Assistant instala la dependencia
PDF declarada en el manifest; el usuario no instala lectores por terminal.

## Privacidad y soporte

Las credenciales se guardan en la entrada de configuración de Home Assistant y
se envían a los servidores de Niba para autenticar la cuenta. No publiques
registros o diagnósticos sin retirar correo, CUPS, identificadores y cualquier
otro dato personal.

Para incidencias, actualiza la integración y reinicia Home Assistant antes de
abrir un [issue en GitHub](https://github.com/Home24alberto/home-assistant-niba/issues).
Incluye la versión de Home Assistant y de la integración, el error relevante y
los nombres de endpoint/estado HTTP si aparecen; nunca incluyas credenciales,
tokens ni CUPS.

Licencia: [MIT](LICENSE).
