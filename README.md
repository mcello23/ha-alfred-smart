# Alfred Smart para Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/__GITHUB_USER__/ha-alfred-smart/actions/workflows/validate.yml/badge.svg)](https://github.com/__GITHUB_USER__/ha-alfred-smart/actions/workflows/validate.yml)

🇪🇸 Español · [🇬🇧 English](README.en.md)

Integración **no oficial** de [Alfred Smart](https://alfredsmart.com) para Home
Assistant: abre los portales, las puertas de garaje y las zonas comunes de tu
edificio desde Home Assistant, con descubrimiento automático de todos los
accesos de tu cuenta.

> [!IMPORTANT]
> Este proyecto no está afiliado a Alfred Smart Systems S.L. ni cuenta con su
> respaldo. Usa la misma API que la aplicación web
> ([app.alfredsmart.com](https://app.alfredsmart.com)), que no está documentada
> y puede cambiar sin previo aviso. Úsala bajo tu responsabilidad: estas
> entidades abren puertas de verdad.

## Qué hace

- **Inicio de sesión con tu correo y contraseña**, igual que en la app. La
  sesión se renueva sola: no hace falta copiar tokens a mano ni programar
  renovaciones.
- **Descubrimiento automático** de todos los accesos de tu vivienda, incluidos
  los compartidos con la comunidad: portales, garajes, puertas de piscina…
- **Un botón «Abrir» por acceso**, agrupado en dispositivos con su gateway.
- **Errores que se ven**: si Alfred no consigue abrir, el botón falla con un
  mensaje claro en lugar de fingir que ha ido bien.
- **Sensor «Última apertura»** por acceso, para saber si la última vez funcionó
  y, si no, por qué.
- **Conexión con Alfred** como sensor binario de diagnóstico, tolerante a los
  cortes de un solo ciclo.
- **Zonas comunes**: servicios para reservar una franja y abrir la puerta del
  gimnasio, la sala comunitaria, etc.
- Interfaz en **español, inglés y portugués (Brasil)**.

## Instalación

### Con HACS (recomendado)

1. En HACS, menú ⋮ → **Repositorios personalizados**.
2. Añade `https://github.com/__GITHUB_USER__/ha-alfred-smart` con la categoría
   **Integración**.
3. Busca **Alfred Smart**, descárgala y reinicia Home Assistant.

[![Abrir en HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=__GITHUB_USER__&repository=ha-alfred-smart&category=integration)

### Manual

Copia la carpeta `custom_components/alfred_smart` en la carpeta
`custom_components` de tu configuración y reinicia Home Assistant.

## Configuración

**Ajustes → Dispositivos y servicios → Añadir integración → Alfred Smart.**

1. Introduce el correo y la contraseña de tu cuenta de Alfred.
2. La integración busca tus viviendas:
   - si solo hay una, se configura directamente;
   - si hay varias, eliges una (repite el proceso para las demás);
   - si no consigue descubrirla, te pide el **código de la vivienda**
     (`asset_id`). Ver [Cómo encontrar el código de la vivienda](#cómo-encontrar-el-código-de-la-vivienda).

En **Configurar** puedes cambiar cada cuántos minutos se consulta la lista de
accesos (5 por defecto). Abrir no depende de este intervalo: el botón llama a
Alfred en el momento.

## Entidades

| Entidad | Tipo | Qué es |
| --- | --- | --- |
| `button.<acceso>_abrir` | Botón | Abre el portal, garaje o puerta. El icono depende del tipo, que se deduce del nombre. |
| `sensor.<acceso>_ultima_apertura` | Enum | `ok`, `gateway_unreachable`, `unauthorized`, `forbidden`, `timeout` o `error`. Atributos: `last_attempt` y `http_status`. |
| `binary_sensor.<vivienda>_conexion_con_alfred` | Conectividad | Si la nube de Alfred responde. Diagnóstico. |

Los accesos que Alfred marca como desactivados aparecen como no disponibles.
Si la administración añade un acceso nuevo, aparece solo en el siguiente
ciclo; si lo quita, puedes borrar el dispositivo desde su página.

**¿Por qué un botón y no una cerradura o una persiana?** Alfred solo envía un
impulso de apertura; nunca informa de si la puerta está abierta o cerrada. Un
`lock` o un `cover` tendrían que inventarse el estado.

## Servicios

### `alfred_smart.book_common_area`

Reserva una franja en una zona común. Las horas se interpretan en la zona
horaria de Home Assistant.

```yaml
action: alfred_smart.book_common_area
data:
  common_area_id: 00000000-0000-4000-8000-000000000001
  start: "2026-10-05 07:00:00"
  end: "2026-10-05 07:30:00"
response_variable: reserva
```

### `alfred_smart.open_common_area`

Abre la puerta de una zona común (normalmente exige una reserva en vigor). Si
la integración ha descubierto la zona, `sensor_uuid` es opcional.

```yaml
action: alfred_smart.open_common_area
data:
  common_area_id: 00000000-0000-4000-8000-000000000001
  sensor_uuid: 00000000-0000-4000-8000-0000000000aa
```

Con más de una vivienda configurada, añade `config_entry_id` para indicar cuál.

### Ejemplo: entrar a pie por dos portales

```yaml
script:
  entrada_a_pie:
    alias: Entrada a pie
    mode: single
    sequence:
      - action: button.press
        target:
          entity_id: button.portal_1_abrir
      - delay: "00:00:30"
      - action: button.press
        target:
          entity_id: button.portal_3_abrir
```

Si el primer portal falla, el script se detiene con el error en lugar de abrir
el segundo para nada.

## Cuando algo no abre

| Lo que ves | Qué significa | Qué hacer |
| --- | --- | --- |
| **Gateway sin conexión** (HTTP 502/503/504) | El servidor de Alfred no consigue hablar con el gateway físico del portal. Lo normal es que otros portales sí abran. | Nada que arreglar en Home Assistant. Usa la llave y avisa al administrador de la finca: el gateway está caído o atascado. Un firmware más antiguo que el de los demás (en la página del dispositivo del gateway) es buena pista. |
| **Sesión rechazada** (HTTP 401) | La contraseña ha cambiado. | Home Assistant mostrará una notificación para volver a iniciar sesión. |
| **Sin permiso** (HTTP 403) | Tu cuenta ve el acceso pero no puede usarlo. | Habla con la administración. |
| **Sin respuesta** | Alfred no ha contestado a tiempo (30 s). | **Puede que se haya abierto igualmente.** Compruébalo antes de volver a pulsar. La integración nunca reintenta una apertura por su cuenta. |

**«Activado» no significa «funciona».** Un portal con el gateway caído sigue
apareciendo como activado en la lista de Alfred, así que la integración no
puede saberlo hasta que intentas abrir. Por eso existe el sensor
«Última apertura».

**Un sondeo perdido no es una caída.** La nube de Alfred falla de vez en cuando
una petición suelta. La integración conserva los últimos datos durante 15
minutos antes de dar las entidades por no disponibles, para no generar falsas
alarmas.

## Cómo encontrar el código de la vivienda

Solo hace falta si el descubrimiento automático no la encuentra:

1. Abre [app.alfredsmart.com](https://app.alfredsmart.com) en el navegador del
   ordenador e inicia sesión.
2. Abre las herramientas de desarrollo (F12) → pestaña **Red**.
3. Entra en tu vivienda y busca una petición a `devices`.
4. El valor del parámetro `asset_id=` es el código (algo como `ABCD1234EFGHI`).

## Script de descubrimiento

`scripts/alfred_discover.py` lista, sin Home Assistant, todo lo que la
integración vería en tu cuenta. **Solo lee**: nunca abre ni reserva nada. Solo
necesita Python 3.

```bash
python3 scripts/alfred_discover.py --email tu@correo.es
python3 scripts/alfred_discover.py --asset ABCD1234EFGHI --dump alfred.json
```

`--dump` guarda las respuestas en bruto con los datos personales censurados.
Si algo no se descubre bien en tu edificio, adjunta ese archivo (o el
**diagnóstico** que se descarga desde la página de la integración) en una
incidencia: es la forma de mejorar el descubrimiento para todos.

## Limitaciones conocidas

- La API no es pública. Si Alfred la cambia, la integración puede dejar de
  funcionar hasta que se actualice.
- No hay estado de las puertas (abierta/cerrada), solo el impulso de apertura.
- Los gateways no se pueden reiniciar: como residente, la API solo permite
  leerlos.
- El listado automático de zonas comunes aún no está confirmado en todos los
  edificios. Si tu zona no aparece, los servicios funcionan igualmente con su
  identificador.

## Privacidad

El correo y la contraseña se guardan en la configuración de Home Assistant
(igual que en cualquier integración en la nube) y solo se envían a
`services.alfredsmartdata.com`. El token de sesión vive en memoria y no se
guarda en el historial. El diagnóstico censura correo, contraseña, token,
direcciones y teléfonos.

## Contribuir

```bash
pip install pytest-homeassistant-custom-component   # instala también Home Assistant
pytest
```

Con solo `pip install aiohttp pytest` se ejecutan los tests del cliente de la
API; los de la integración completa se omiten.

Las incidencias y las *pull requests* son bienvenidas, en español, inglés o
portugués.

## Licencia

[MIT](LICENSE)
