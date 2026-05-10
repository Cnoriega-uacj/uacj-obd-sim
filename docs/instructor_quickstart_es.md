# Guía Rápida del Instructor

**Para el programa automotriz de UACJ — imprime esta hoja y déjala junto al simulador.**

Una clase completa se puede correr con tres botones. La referencia
completa está en `docs/instructor.md`; ésta es la hoja de un solo
lado.

---

## Antes de clase (1 minuto)

1. Energiza la placa Pi del simulador (12 V por el pin 16 del
   conector OBD-II, o USB-C).
2. En la laptop, doble clic a **`start_uacj.bat`** (Windows) o
   **`./start_uacj.sh`** (Mac/Linux).
3. El panel se abre en <http://localhost:8000>.

> Cambia de idioma en cualquier momento con el botón **EN/ES** arriba
> a la derecha.

---

## Cargar un escenario didáctico (3 botones)

1. Abre **Escenarios** en la barra superior.
2. En el panel **Nuevo desde plantilla**, selecciona:
    - una **plantilla** (ej. *P0420 catalizador*, *P0171 mezcla pobre*,
      *P0301 falla de cilindro*, *P0455 fuga EVAP*,
      *Ciclo de manejo incompleto*, *U0100 pérdida de comunicación*)
    - una **sesión origen** (ej. *Silverado 2008* — provee los datos
      en vivo sobre los que se monta el escenario)
3. Haz clic en **Crear** → aparece un nuevo escenario en la lista.
4. Selecciónalo y haz clic en **Enviar al simulador**.

El Pi ya responde a las herramientas de escaneo como ese vehículo.
Los alumnos pueden conectar sus scanners.

---

## Durante la clase

| Pestaña | Propósito |
|---|---|
| **Adquisición** | Lecturas en vivo, DTCs, monitores, lista de vehículos capturados |
| **Escenarios** | Crear y editar escenarios didácticos |
| **Aula** | Registro en vivo — ve lo que pregunta cada scanner del alumno |
| **Comparar** | Comparación lado a lado de dos sesiones capturadas |

La vista **Aula** se actualiza una vez por segundo. Mírala durante
la clase — cada solicitud aparece con un indicador de color (verde
positivo, rojo NRC, amarillo advertencia).

---

## Seis plantillas integradas para empezar

| Plantilla | Enseña |
|---|---|
| **P0420** | Eficiencia del catalizador bajo umbral — diagnóstico de cat 3-vías |
| **P0171** | Sistema mezcla pobre banco 1 — STFT/LTFT + MAF/vacío |
| **P0301 + P0300** | Falla cilindro 1 + falla aleatoria — bobina/inyector/compresión |
| **P0455** | Fuga grande sistema EVAP — tapón, manguera, válvula de purga |
| **Ciclo de manejo incompleto** | Monitores no listos — preparación para verificación |
| **U0100** | Pérdida de comunicación con ECM — red/cableado |

---

## Guardar un vehículo real para usarlo después

1. Conecta el **OBDLink SX** al USB de la laptop y al vehículo.
2. **Adquisición** → adaptador `elm327`, puerto (`COM3` en Windows;
   `/dev/ttyUSB0` en Linux/Mac), clic en **Iniciar**.
3. Corre de 30 a 60 segundos (en ralentí está bien). Clic en **Detener**.
4. El vehículo queda guardado en `data/sessions/{VIN}_{marca}_{modelo}_{año}/`.
5. A partir de ahora aparece como "Sesión origen" al crear escenarios.

---

## Respaldo al final del semestre

Clic en **Respaldar todo** en la barra izquierda. Descarga un ZIP
con la base de datos completa + cada sesión. Guárdalo en una USB.
Para restaurar en otra laptop, corre el lanzador una vez, clic en
**Restaurar respaldo**, selecciona el ZIP.

---

## Cuando algo falla

| Síntoma | Primer paso |
|---|---|
| El panel dice "ningún vehículo conectado" | Revisa el cable del OBDLink SX; gira la llave del auto a "encendido" |
| "Enviar al simulador" se queda esperando | En la vista Aula, verifica la URL del simulador; revisa que el Pi tenga energía |
| El scanner dice "sin comunicación" | Verifica con multímetro que haya 12 V en pin 16 del OBD del simulador |
| El DTC que lee el alumno no es el del escenario | Re-envía el escenario (el Pi pudo haber borrado códigos tras un modo 04) |
| El Pi no se ve en `uacj-sim.local` | Usa la IP directa — sácala de la página admin del router o `hostname -I` por SSH |

---

*Simulador OBD-II UACJ v0.4.0 — guía rápida. Imprime en A4 o Carta;
el formato cabe en una hoja por un solo lado.*
