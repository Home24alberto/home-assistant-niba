# Niba Energía para Home Assistant

Integración comunitaria de Home Assistant para consultar los datos disponibles
en la cuenta de cliente de **Niba Energía**.

> [!IMPORTANT]
> Este proyecto no es oficial y no está afiliado, respaldado ni mantenido por
> Niba Energía. Utiliza una API privada que puede cambiar sin previo aviso.

## Funciones

- Configuración desde la interfaz con correo electrónico y contraseña.
- Actualización automática cada 15 minutos.
- Consumo del periodo actual, estimaciones y comparación con el periodo anterior.
- Consumo diario, importación, exportación y perfil horario disponible.
- Datos mensuales, potencia y autoconsumo disponibles en la cuenta.
- Saldo, movimientos y batería solar de Niba.
- Contrato, producto, precios, potencia contratada y datos técnicos.
- Última factura y desglose de sus principales conceptos.
- Reautenticación desde Home Assistant si caduca la sesión.

La disponibilidad de cada entidad depende de los servicios contratados y de los
datos que la API devuelva para cada cuenta.

## Instalación con HACS

Mientras el repositorio no figure en el catálogo general de HACS:

1. Abre **HACS** en Home Assistant.
2. Entra en **Integraciones**.
3. Abre el menú y selecciona **Repositorios personalizados**.
4. Añade `https://github.com/Home24alberto/home-assistant-niba` como categoría
   **Integración**.
5. Busca **Niba Energía** e instálala.
6. Reinicia Home Assistant.

## Configuración

1. Abre **Ajustes → Dispositivos y servicios**.
2. Pulsa **Añadir integración**.
3. Busca **Niba Energía**.
4. Introduce el correo electrónico y la contraseña de tu cuenta de cliente.

La integración detectará el contrato y el CUPS automáticamente. Solo se admite
una cuenta de Niba por instalación de Home Assistant.

## Actualizaciones

HACS avisará cuando exista una versión nueva. Tras actualizar los archivos,
reinicia Home Assistant para cargar la nueva versión.

## Privacidad

- El repositorio no contiene credenciales, tokens, CUPS ni datos de clientes.
- Las credenciales introducidas se guardan en la entrada de configuración de
  Home Assistant y se envían únicamente a los servidores de Niba para iniciar
  sesión.
- Algunas entidades de diagnóstico pueden mostrar información personal del
  contrato dentro de tu propio Home Assistant.
- No publiques registros, diagnósticos o capturas sin revisar antes sus datos.

## Solución de problemas

Antes de abrir una incidencia:

1. Comprueba que puedes entrar con las mismas credenciales en el área de cliente
   o aplicación oficial de Niba.
2. Actualiza la integración desde HACS.
3. Reinicia Home Assistant.
4. Revisa **Ajustes → Sistema → Registros**.

Las incidencias se pueden comunicar en
[GitHub Issues](https://github.com/Home24alberto/home-assistant-niba/issues).

## Licencia

Distribuido bajo la licencia [MIT](LICENSE).
