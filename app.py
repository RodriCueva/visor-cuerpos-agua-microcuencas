
from pathlib import Path

import altair as alt
import folium
import geopandas as gpd
import pandas as pd
import pyogrio
import streamlit as st

from folium.plugins import Fullscreen
from shapely.geometry import Point
from streamlit_folium import st_folium


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Visor de cuerpos de agua por microcuenca",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded"
)

DATOS = Path(__file__).resolve().parent / "datos"

ARCHIVO_MICRO = DATOS / "TB_MICROCUENCAS.gpkg"
ARCHIVO_RIVERS = DATOS / "TB_RIVERS.gpkg"
ARCHIVO_VISOR = DATOS / "visor_cuerpos_agua.gpkg"

for archivo in [
    ARCHIVO_MICRO,
    ARCHIVO_RIVERS,
    ARCHIVO_VISOR
]:
    if not archivo.exists():
        st.error(f"No se encontró el archivo: {archivo.name}")
        st.stop()


# ============================================================
# CONFIGURACIÓN TEMÁTICA
# ============================================================

UH_NOMBRES = {
    1: "Huaura",
    2: "Mantaro",
    3: "Huaral",
    4: "Chillón",
    5: "Rímac",
    6: "Lurín",
    7: "Mala",
    8: "Cañete",
    9: "Alto Huallaga",
    10: "Perené",
    11: "Pachitea"
}

COLOR_CON_AGUA = "#2878B8"
COLOR_SIN_AGUA = "#57966C"
COLOR_AGUA = "#168BDB"
COLOR_RIVERS = "#0756A3"

TILES = {
    "Relieve topográfico (Esri)": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "Tiles © Esri"
    ),
    "CartoDB claro": ("CartoDB positron", "CartoDB"),
    "OpenStreetMap": ("OpenStreetMap", "OpenStreetMap"),
    "Sin mapa base": (None, None)
}


# ============================================================
# FUNCIONES DE MAPA
# ============================================================

def crear_mapa(base):

    tiles, attr = TILES[base]

    mapa = folium.Map(
        location=[-11.3, -76.5],
        zoom_start=7,
        tiles=tiles,
        attr=attr,
        control_scale=True,
        prefer_canvas=True
    )

    Fullscreen(position="topright").add_to(mapa)

    return mapa


# ============================================================
# LECTURA DE MICROCUENCAS ORIGINALES
# ============================================================

@st.cache_data(show_spinner="Leyendo delimitaciones originales...")
def leer_microcuencas(path, mtime):

    capas = pyogrio.list_layers(path)

    partes = []

    for fila in capas:

        nombre = fila[0]

        info = pyogrio.read_info(
            path,
            layer=nombre
        )

        # Ignorar tablas auxiliares sin geometría
        if not info.get("geometry_type"):
            continue

        gdf = pyogrio.read_dataframe(
            path,
            layer=nombre
        )

        if not gdf.empty:
            partes.append(gdf)

    if not partes:
        raise ValueError(
            "No se encontraron capas espaciales en TB_MICROCUENCAS."
        )

    micro = gpd.GeoDataFrame(
        pd.concat(partes, ignore_index=True),
        geometry="geometry",
        crs=partes[0].crs
    )

    if "ID_MICRO" not in micro.columns:
        raise ValueError(
            "TB_MICROCUENCAS no contiene ID_MICRO."
        )

    if "ID_UH" not in micro.columns:
        raise ValueError(
            "TB_MICROCUENCAS no contiene ID_UH."
        )

    micro["ID_MICRO"] = pd.to_numeric(
        micro["ID_MICRO"],
        errors="raise"
    ).astype("int64")

    micro["ID_UH"] = pd.to_numeric(
        micro["ID_UH"],
        errors="raise"
    ).astype("int64")

    if micro["ID_MICRO"].duplicated().any():
        raise ValueError(
            "Existen ID_MICRO duplicados en las capas originales."
        )

    return micro


# ============================================================
# ATRIBUTOS DE CONTROL DEL VISOR INTEGRADO
# ============================================================

@st.cache_data(show_spinner=False)
def leer_control(path, mtime):

    control = pyogrio.read_dataframe(
        path,
        layer="MICROCUENCAS",
        columns=[
            "ID_MICRO",
            "TIENE_AGUA",
            "N_AGUA",
            "AREA_AGUA_KM2",
            "PCT_AGUA",
            "N_IGN",
            "N_INAIGEM",
            "AREA_IGN_KM2",
            "AREA_INAIGEM_KM2"
        ],
        read_geometry=False
    )

    control["ID_MICRO"] = control["ID_MICRO"].astype("int64")

    return control.drop_duplicates("ID_MICRO")


@st.cache_data(show_spinner="Preparando mapa de microcuencas...")
def microcuencas_uh(
    path_micro,
    mtime_micro,
    path_control,
    mtime_control,
    id_uh
):

    micro = leer_microcuencas(
        path_micro,
        mtime_micro
    )

    control = leer_control(
        path_control,
        mtime_control
    )

    micro = micro.merge(
        control,
        on="ID_MICRO",
        how="left",
        validate="one_to_one"
    )

    micro["TIENE_AGUA"] = (
        micro["TIENE_AGUA"]
        .fillna(0)
        .astype("int64")
    )

    micro = micro[micro["ID_UH"] == int(id_uh)].copy()

    return micro.to_crs(4326)


@st.cache_data(show_spinner=False, max_entries=100)
def microcuenca_detalle(
    path_micro,
    mtime_micro,
    path_control,
    mtime_control,
    mid
):

    micro = microcuencas_uh(
        path_micro,
        mtime_micro,
        path_control,
        mtime_control,
        int(
            leer_microcuencas(
                path_micro,
                mtime_micro
            ).loc[
                lambda x: x["ID_MICRO"] == int(mid),
                "ID_UH"
            ].iloc[0]
        )
    )

    return micro[micro["ID_MICRO"] == int(mid)].copy()


# ============================================================
# CUERPOS DE AGUA INTERSECTADOS
# ============================================================

@st.cache_data(show_spinner=False, max_entries=100)
def agua_micro(path, mtime, mid):

    return pyogrio.read_dataframe(
        path,
        layer="AGUA_INTERSECCION",
        where=f'"ID_MICRO" = {int(mid)}'
    )


# ============================================================
# RED DE DRENAJE
# ============================================================

@st.cache_data(show_spinner="Consultando red de drenaje...", max_entries=100)
def rivers_micro(path, mtime, mid):

    capas = pyogrio.list_layers(path)

    partes = []

    for fila in capas:

        nombre = fila[0]

        info = pyogrio.read_info(
            path,
            layer=nombre
        )

        if not info.get("geometry_type"):
            continue

        campos = info.get("fields")

        if campos is None or "ID_MICRO" not in campos:
            continue

        try:

            rios = pyogrio.read_dataframe(
                path,
                layer=nombre,
                where=f'"ID_MICRO" = {int(mid)}'
            )

        except Exception:

            rios = pyogrio.read_dataframe(
                path,
                layer=nombre
            )

            if "ID_MICRO" not in rios.columns:
                continue

            rios = rios[
                pd.to_numeric(
                    rios["ID_MICRO"],
                    errors="coerce"
                ) == int(mid)
            ]

        if not rios.empty:
            partes.append(rios)

    if not partes:
        return gpd.GeoDataFrame(
            geometry=[],
            crs="EPSG:32718"
        )

    rios = gpd.GeoDataFrame(
        pd.concat(partes, ignore_index=True),
        geometry="geometry",
        crs=partes[0].crs
    )

    return rios


# ============================================================
# ESTILOS
# ============================================================

st.markdown("""
<style>

.block-container {
    padding-top: 1.25rem;
    padding-bottom: 2rem;
    max-width: 1550px;
}

[data-testid="stMetric"] {
    border: 1px solid rgba(140,150,150,.22);
    border-radius: 12px;
    padding: 12px 15px;
}

.legend-row {
    display: flex;
    align-items: center;
    gap: 9px;
    margin: 8px 0;
    font-size: .9rem;
}

.legend-swatch {
    width: 15px;
    height: 15px;
    border: 1px solid #777;
    border-radius: 3px;
    flex: none;
}

</style>
""", unsafe_allow_html=True)


# ============================================================
# ENCABEZADO
# ============================================================

st.title("Visor de cuerpos de agua por microcuenca")

st.caption(
    "Inventario de cuerpos de agua · 11 unidades hidrográficas · "
    "Distribución y superficie por microcuenca"
)

st.info(
    "La delimitación de microcuencas corresponde a las geometrías "
    "originales. El color azul identifica microcuencas con cuerpos "
    "de agua y el verde aquellas sin registros."
)


# ============================================================
# CARGA DE INVENTARIO Y CONTROL
# ============================================================

try:

    micros_originales = leer_microcuencas(
        str(ARCHIVO_MICRO),
        ARCHIVO_MICRO.stat().st_mtime
    )

    control = leer_control(
        str(ARCHIVO_VISOR),
        ARCHIVO_VISOR.stat().st_mtime
    )

    inventario = micros_originales[
        ["ID_MICRO", "ID_UH"]
    ].merge(
        control[["ID_MICRO", "TIENE_AGUA"]],
        on="ID_MICRO",
        how="left",
        validate="one_to_one"
    )

    inventario["TIENE_AGUA"] = (
        inventario["TIENE_AGUA"]
        .fillna(0)
        .astype("int64")
    )

except Exception as exc:

    st.exception(exc)
    st.stop()


uhs = sorted(inventario["ID_UH"].unique().tolist())


# ============================================================
# ESTADO DE NAVEGACIÓN
# ============================================================

if "agua_uh_actual" not in st.session_state:
    st.session_state.agua_uh_actual = uhs[0]

if "agua_id_actual" not in st.session_state:
    st.session_state.agua_id_actual = None

if "agua_vista_detalle" not in st.session_state:
    st.session_state.agua_vista_detalle = False


# ============================================================
# BARRA LATERAL
# ============================================================

with st.sidebar:

    st.header("Consulta")

    uh_id = st.selectbox(
        "Unidad hidrográfica",
        uhs,
        format_func=lambda v: UH_NOMBRES.get(v, str(v)),
        key="agua_uh_actual"
    )

    uh = UH_NOMBRES.get(uh_id, str(uh_id))

    ids = sorted(
        inventario.loc[
            inventario.ID_UH == uh_id,
            "ID_MICRO"
        ].unique().tolist()
    )

    st.caption(
        f"{len(ids):,} microcuencas disponibles"
    )

    buscar = st.text_input(
        "Buscar ID_MICRO",
        placeholder="Ej. 200005"
    )

    if st.button(
        "Buscar microcuenca",
        use_container_width=True
    ):

        try:

            mid_buscado = int(buscar.strip())

            if mid_buscado in ids:

                st.session_state.agua_id_actual = mid_buscado
                st.session_state.agua_vista_detalle = True

                st.rerun()

            else:

                st.warning(
                    "El ID no pertenece a la unidad hidrográfica seleccionada."
                )

        except ValueError:

            st.warning("Introduce un identificador numérico.")

    if st.button(
        "Mostrar todas las microcuencas",
        use_container_width=True
    ):

        st.session_state.agua_vista_detalle = False
        st.session_state.agua_id_actual = None

        st.rerun()

    st.divider()

    st.subheader("Cartografía")

    base = st.selectbox(
        "Mapa base",
        list(TILES),
        index=0
    )

    st.markdown("**Leyenda**")

    st.markdown(
        f'<div class="legend-row">'
        f'<span class="legend-swatch" style="background:{COLOR_CON_AGUA}"></span>'
        f'<span>Con cuerpos de agua</span></div>'
        f'<div class="legend-row">'
        f'<span class="legend-swatch" style="background:{COLOR_SIN_AGUA}"></span>'
        f'<span>Sin cuerpos de agua</span></div>'
        f'<div class="legend-row">'
        f'<span class="legend-swatch" style="background:{COLOR_AGUA}"></span>'
        f'<span>Cuerpos de agua</span></div>'
        f'<div class="legend-row">'
        f'<span class="legend-swatch" style="background:{COLOR_RIVERS}"></span>'
        f'<span>Red de drenaje</span></div>',
        unsafe_allow_html=True
    )


# ============================================================
# VALIDACIÓN DE SELECCIÓN
# ============================================================

if st.session_state.agua_id_actual not in ids:

    st.session_state.agua_id_actual = None
    st.session_state.agua_vista_detalle = False


# ============================================================
# VISTA GENERAL
# ============================================================

if not st.session_state.agua_vista_detalle:

    st.subheader(f"Microcuencas de {uh}")

    st.caption(
        "Selecciona una microcuenca directamente en el mapa "
        "para consultar sus cuerpos de agua y red de drenaje."
    )

    try:

        general = microcuencas_uh(
            str(ARCHIVO_MICRO),
            ARCHIVO_MICRO.stat().st_mtime,
            str(ARCHIVO_VISOR),
            ARCHIVO_VISOR.stat().st_mtime,
            uh_id
        )

    except Exception as exc:

        st.exception(exc)
        st.stop()

    if general.empty:

        st.warning("No hay microcuencas para esta unidad hidrográfica.")
        st.stop()

    # Se utilizan las geometrías originales, sin simplificación.
    mapa = crear_mapa(base)

    folium.GeoJson(
        general.to_json(),
        name="Delimitación original de microcuencas",
        style_function=lambda f: {
            "fillColor": (
                COLOR_CON_AGUA
                if int(f["properties"]["TIENE_AGUA"]) == 1
                else COLOR_SIN_AGUA
            ),
            "fillOpacity": 0.58,
            "color": "#34454F",
            "weight": 0.65
        },
        highlight_function=lambda f: {
            "fillColor": "#E6B85C",
            "fillOpacity": 0.85,
            "color": "#A66A20",
            "weight": 2
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["ID_MICRO", "TIENE_AGUA"],
            aliases=[
                "ID_MICRO:",
                "Tiene cuerpos de agua (1=sí, 0=no):"
            ],
            sticky=True
        )
    ).add_to(mapa)

    minx, miny, maxx, maxy = general.total_bounds

    mapa.fit_bounds([
        [miny, minx],
        [maxy, maxx]
    ])

    evento = st_folium(
        mapa,
        height=660,
        use_container_width=True,
        key=f"agua_general_{uh_id}_{base}",
        returned_objects=["last_clicked"]
    )

    click = evento.get("last_clicked") if evento else None

    if click and "lat" in click and "lng" in click:

        punto = Point(
            click["lng"],
            click["lat"]
        )

        posibles = general[
            general.geometry.intersects(punto)
        ]

        if not posibles.empty:

            st.session_state.agua_id_actual = int(
                posibles.iloc[0]["ID_MICRO"]
            )

            st.session_state.agua_vista_detalle = True

            st.rerun()

    st.caption(
        "Azul: microcuencas con cuerpos de agua. "
        "Verde: microcuencas sin cuerpos de agua. "
        "La geometría representada procede de TB_MICROCUENCAS."
    )


# ============================================================
# VISTA INDIVIDUAL
# ============================================================

else:

    mid = int(st.session_state.agua_id_actual)

    try:

        micro = microcuenca_detalle(
            str(ARCHIVO_MICRO),
            ARCHIVO_MICRO.stat().st_mtime,
            str(ARCHIVO_VISOR),
            ARCHIVO_VISOR.stat().st_mtime,
            mid
        )

        agua = agua_micro(
            str(ARCHIVO_VISOR),
            ARCHIVO_VISOR.stat().st_mtime,
            mid
        )

        rios = rivers_micro(
            str(ARCHIVO_RIVERS),
            ARCHIVO_RIVERS.stat().st_mtime,
            mid
        )

    except Exception as exc:

        st.exception(exc)
        st.stop()

    if micro.empty:

        st.warning("No se encontró la microcuenca.")
        st.stop()

    registro = micro.iloc[0]

    area_micro = float(registro["AREA_KM2"])
    area_agua = float(registro["AREA_AGUA_KM2"])
    n_agua = int(registro["N_AGUA"])
    cobertura = float(registro["PCT_AGUA"])

    st.subheader(f"Microcuenca {mid}")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Área de microcuenca",
        f"{area_micro:,.3f} km²"
    )

    c2.metric(
        "Cuerpos de agua",
        f"{n_agua:,}"
    )

    c3.metric(
        "Superficie de agua",
        f"{area_agua:,.4f} km²"
    )

    c4.metric(
        "Cobertura de agua",
        f"{cobertura:.2f} %"
    )

    st.divider()

    # --------------------------------------------------------
    # MAPA INDIVIDUAL
    # --------------------------------------------------------

    col_mapa, col_datos = st.columns(
        [1.2, 1.0],
        gap="medium"
    )

    with col_mapa:

        st.markdown("#### Distribución espacial")

        mapa = crear_mapa(base)

        micro_4326 = micro.to_crs(4326)

        # Cuerpos de agua
        if not agua.empty:

            agua_4326 = agua.to_crs(4326)

            folium.GeoJson(
                agua_4326.to_json(),
                name="Cuerpos de agua",
                style_function=lambda f: {
                    "fillColor": COLOR_AGUA,
                    "fillOpacity": 0.78,
                    "color": "#075A91",
                    "weight": 0.8
                },
                tooltip=folium.GeoJsonTooltip(
                    fields=[
                        "ID_AGUA",
                        "FUENTE",
                        "AREA_AGUA_KM2"
                    ],
                    aliases=[
                        "ID de cuerpo de agua:",
                        "Fuente:",
                        "Área intersectada (km²):"
                    ]
                )
            ).add_to(mapa)

        # Red de drenaje: solo en la ficha individual
        if not rios.empty:

            rios_4326 = rios.to_crs(4326)

            folium.GeoJson(
                rios_4326.to_json(),
                name="Red de drenaje",
                style_function=lambda f: {
                    "color": COLOR_RIVERS,
                    "weight": 1.7,
                    "opacity": 0.9
                },
                tooltip=folium.GeoJsonTooltip(
                    fields=[
                        c for c in ["ID_RIVER", "ID_DOWN"]
                        if c in rios_4326.columns
                    ],
                    aliases=[
                        c + ":" for c in ["ID_RIVER", "ID_DOWN"]
                        if c in rios_4326.columns
                    ]
                ) if any(
                    c in rios_4326.columns
                    for c in ["ID_RIVER", "ID_DOWN"]
                ) else None
            ).add_to(mapa)

        # Límite original de la microcuenca
        folium.GeoJson(
            micro_4326.to_json(),
            name="Límite de microcuenca",
            style_function=lambda f: {
                "fillOpacity": 0,
                "color": "#263B45",
                "weight": 2.5
            },
            interactive=False
        ).add_to(mapa)

        minx, miny, maxx, maxy = micro_4326.total_bounds

        dx = max(maxx - minx, 0.002)
        dy = max(maxy - miny, 0.002)

        mapa.fit_bounds([
            [miny - dy * 0.12, minx - dx * 0.12],
            [maxy + dy * 0.12, maxx + dx * 0.12]
        ])

        folium.LayerControl().add_to(mapa)

        st_folium(
            mapa,
            height=625,
            use_container_width=True,
            key=f"agua_detalle_{mid}_{base}",
            returned_objects=[]
        )

        st.caption(
            "Azul: cuerpos de agua. Naranja: red de drenaje. "
            "Contorno oscuro: delimitación original de la microcuenca."
        )

        st.caption(
            f"Tramos de drenaje asociados: {len(rios):,}"
        )

    # --------------------------------------------------------
    # GRÁFICO: PORCENTAJE DE AGUA
    # --------------------------------------------------------

    with col_datos:

        st.markdown("#### Porcentaje de superficie de agua")

        grafico_df = pd.DataFrame({
            "Indicador": ["Cobertura de agua"],
            "Porcentaje": [cobertura]
        })

        max_eje = max(100.0, cobertura * 1.05)

        grafico = alt.Chart(grafico_df).mark_bar(
            color="#2878B8",
            cornerRadiusEnd=5,
            size=42
        ).encode(
            x=alt.X(
                "Porcentaje:Q",
                title="Porcentaje (%)",
                scale=alt.Scale(
                    domain=[0, max_eje]
                )
            ),
            y=alt.Y(
                "Indicador:N",
                title=None,
                axis=alt.Axis(labels=False, ticks=False)
            ),
            tooltip=[
                alt.Tooltip(
                    "Porcentaje:Q",
                    title="Cobertura",
                    format=".4f"
                )
            ]
        ).properties(
            height=125
        )

        st.altair_chart(
            grafico,
            use_container_width=True
        )

        st.metric(
            "Porcentaje de la microcuenca ocupado por agua",
            f"{cobertura:.4f} %"
        )

        st.markdown("#### Distribución por fuente")

        # Tabla: mantiene la separación de fuentes.
        if agua.empty:

            st.info(
                "Esta microcuenca no presenta cuerpos de agua registrados."
            )

        else:

            tabla = pd.DataFrame(
                agua.drop(columns="geometry")
            )

            columnas = [
                "ID_AGUA",
                "FUENTE",
                "TIPO",
                "AREA_AGUA_M2",
                "AREA_AGUA_KM2",
                "PCT_MICRO"
            ]

            disponibles = [
                c for c in columnas
                if c in tabla.columns
            ]

            tabla = tabla[disponibles].copy()

            nombres = {
                "ID_AGUA": "ID_AGUA",
                "FUENTE": "FUENTE",
                "TIPO": "TIPO",
                "AREA_AGUA_M2": "ÁREA (m²)",
                "AREA_AGUA_KM2": "ÁREA (km²)",
                "PCT_MICRO": "% MICROCUENCA"
            }

            tabla = tabla.rename(columns=nombres)

            tabla = tabla.sort_values(
                "ÁREA (m²)",
                ascending=False
            ) if "ÁREA (m²)" in tabla.columns else tabla

            st.dataframe(
                tabla,
                hide_index=True,
                use_container_width=True
            )

            st.download_button(
                "Descargar tabla CSV",
                data=tabla.to_csv(
                    index=False
                ).encode("utf-8-sig"),
                file_name=f"cuerpos_agua_micro_{mid}.csv",
                mime="text/csv",
                use_container_width=True
            )

    # --------------------------------------------------------
    # CONTROL DE CALIDAD
    # --------------------------------------------------------

    with st.expander(
        "Información técnica y control de calidad",
        expanded=False
    ):

        st.write(
            "La geometría de la microcuenca procede directamente "
            "de TB_MICROCUENCAS.gpkg."
        )

        st.write(
            "Los cuerpos de agua corresponden a los fragmentos "
            "resultantes de la intersección espacial."
        )

        st.write(
            "La red de drenaje se filtra mediante ID_MICRO "
            "y se representa únicamente en esta vista."
        )

        st.write(
            f"Fragmentos de agua: {len(agua):,}"
        )

        st.write(
            f"Tramos de drenaje: {len(rios):,}"
        )


# ============================================================
# FUENTES Y REFERENCIAS
# ============================================================

st.markdown("---")

with st.expander(
    "Fuentes de datos y referencias bibliográficas",
    expanded=False
):

    st.markdown("""
### 1. Instituto Geográfico Nacional (IGN)

IGN - Instituto Geográfico Nacional. (2026).
*Geoportal del IGN: Capa operacional de Hidrografía a escala 1:100K*.
Lima, Perú: Ministerio de Defensa.

Disponible en:
[Geoportal del IGN](https://www.idep.gob.pe/geovisor/VisorDeMapas-3D/)

### 2. Instituto de Investigación en Glaciares y Ecosistemas de Montaña

INAIGEM - Instituto de Investigación en Glaciares y Ecosistemas
de Montaña. (2023).
*Inventario Nacional de Glaciares y Lagunas de Origen Glaciar
Actualizado al año 2020* (Esquema actualizado al 22 de diciembre
de 2023) [Servicio geográfico en línea / Feature Layer].
Ministerio del Ambiente, Perú.

Disponible en:
[Servicio geográfico en ArcGIS](https://www.arcgis.com/home/item.html?id=ea055f40a9084e05b2fc7fd0544468fc#overview)
""")

    st.caption(
        "Las capas espaciales fueron integradas y procesadas "
        "para su caracterización por microcuenca."
    )


# ============================================================
# PIE DE PÁGINA
# ============================================================

st.caption(
    "Visor geoespacial de cuerpos de agua por microcuenca | "
    "Sistema de referencia de origen: EPSG:32718"
)
