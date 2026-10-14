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

## Instalación con HACS

1. En HACS, abre **Integraciones → ⋮ → Repositorios personalizados**.
2. Añade `https://github.com/Home24alberto/home-assistant-niba` con categoría
   **Integración**.
3. Instala **Niba Energía** y reinicia Home Assistant.
4. Ve a **Ajustes → Dispositivos y servicios → Añadir integración**, busca
   **Niba Energía** e inicia sesión.

## Datos de consumo

Niba ofrece un resumen del periodo y, por separado, curvas diarias. El resumen
puede indicar una fecha posterior a la última curva disponible. La integración
conserva esa diferencia: las entidades terminadas en **API** muestran el
resumen de Niba; **Niba Consumo diario** y las entidades de curvas reflejan
únicamente las fechas recibidas en la respuesta diaria. Si Niba publica menos
días, las fechas ausentes no se rellenan ni se estiman como lecturas reales.

Para interpretar los sensores sin mezclar fuentes ni fechas:

| Sensor | Dato que representa |
| --- | --- |
| **Niba Consumo periodo** | Suma de las curvas diarias disponibles; los atributos indican hasta qué fecha llegan. |
| **Niba Consumo periodo API** | Consumo acumulado comunicado por el resumen de Niba y su última fecha de actualización. |
| **Niba Consumo estimado** | Proyección de kWh calculada con las curvas diarias cerradas disponibles. |
| **Niba Consumo estimado API** | Proyección de kWh comunicada por Niba. |
| **Niba Coste energía periodo** | Coste de energía que devuelve Niba; no es el total de la factura. |
| **Niba Estimación energía API** | Coste de energía estimado por Niba; no incluye necesariamente potencia, impuestos u otros conceptos. |
| **Niba Consumo mensual** | Dato del endpoint mensual de Niba, independiente del resumen del periodo y de las curvas. |

En un panel, empareja el coste con los sensores de consumo **API** y muestra
aparte la suma o proyección de curvas, junto con su fecha. Así no se comparan
por error periodos distintos cuando las curvas llegan con retraso.

Cuando una consulta de curvas falla temporalmente, se conserva en memoria la
última respuesta válida del periodo y los atributos de diagnóstico indican si
se están mostrando datos en caché. Reiniciar Home Assistant borra esa caché
temporal; Niba debe volver a publicar las curvas para recuperarlas.

## Costes y previsiones

No hay que introducir una tarifa para usar la integración. El sensor **Niba
Estimación energía API** muestra el coste estimado que devuelve Niba al iniciar
sesión. Ese importe corresponde al coste de energía indicado por la API; no se
presenta como total de factura cuando no incluye potencia, impuestos u otros
conceptos.

La previsión de consumo en kWh se calcula con la media de curvas diarias
cerradas recibidas. Es una proyección orientativa, no una lectura real ni una
previsión completa del importe de la factura. El total definitivo lo establece
Niba en la factura oficial.

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
