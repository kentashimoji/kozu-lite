# -*- coding: utf-8 -*-
"""
電子公図データ抽出Webアプリ (Streamlit版) - 最適化版
"""

import streamlit as st
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon
import xml.etree.ElementTree as ET
from xml.dom import minidom
import zipfile
import io
import tempfile
import os
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# ページ設定
st.set_page_config(
    page_title="電子公図データ抽出ツール",
    page_icon="🗺️",
    layout="wide"
)

class KojiWebExtractor:
    def __init__(self):
        if 'gdf' not in st.session_state:
            st.session_state.gdf = None
    
    def load_shapefile_from_url(self, url):
        """URLからShapefileを読み込み"""
        try:
            # GitHubの生ファイルURLに変換
            if 'github.com' in url and '/blob/' in url:
                url = url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')
            
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            file_obj = io.BytesIO(response.content)
            
            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    # ZIPファイルとして展開
                    with zipfile.ZipFile(file_obj, 'r') as zip_ref:
                        zip_ref.extractall(temp_dir)
                    
                    shp_files = [f for f in os.listdir(temp_dir) if f.endswith('.shp')]
                    if shp_files:
                        return gpd.read_file(os.path.join(temp_dir, shp_files[0]))
                    else:
                        raise Exception("ZIPファイル内にSHPファイルが見つかりません")
                        
                except zipfile.BadZipFile:
                    # 直接SHPファイルとして読み込み
                    file_obj.seek(0)
                    temp_file = os.path.join(temp_dir, "temp_file.shp")
                    with open(temp_file, 'wb') as f:
                        f.write(file_obj.read())
                    return gpd.read_file(temp_file)
                        
        except Exception as e:
            raise Exception(f"Shapefileの読み込みに失敗しました: {str(e)}")
    
    def create_kml_from_geodataframe(self, gdf, name="地番データ"):
        """GeoPandasデータフレームからKMLファイルを作成"""
        try:
            # WGS84に座標変換
            gdf_wgs84 = gdf.to_crs(epsg=4326)
            
            # KMLのルート要素を作成
            kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
            document = ET.SubElement(kml, "Document")
            doc_name = ET.SubElement(document, "name")
            doc_name.text = name
            
            # スタイル定義
            style = ET.SubElement(document, "Style", id="PolygonStyle")
            line_style = ET.SubElement(style, "LineStyle")
            line_color = ET.SubElement(line_style, "color")
            line_color.text = "ff0000ff"
            line_width = ET.SubElement(line_style, "width")
            line_width.text = "2"
            
            poly_style = ET.SubElement(style, "PolyStyle")
            poly_color = ET.SubElement(poly_style, "color")
            poly_color.text = "3300ff00"
            
            # 各レコードでPlacemarkを作成
            for idx, row in gdf_wgs84.iterrows():
                placemark = ET.SubElement(document, "Placemark")
                
                pm_name = ET.SubElement(placemark, "name")
                pm_name.text = str(row['地番']) if '地番' in row else f"地番_{idx}"
                
                description = ET.SubElement(placemark, "description")
                desc_text = ""
                for col in gdf_wgs84.columns:
                    if col != 'geometry':
                        desc_text += f"{col}: {row[col]}<br/>"
                description.text = desc_text
                
                style_url = ET.SubElement(placemark, "styleUrl")
                style_url.text = "#PolygonStyle"
                
                # ジオメトリ処理
                geom = row['geometry']
                if geom.geom_type == 'Polygon':
                    self._add_polygon_to_placemark(placemark, geom)
                elif geom.geom_type == 'MultiPolygon':
                    for poly in geom.geoms:
                        self._add_polygon_to_placemark(placemark, poly)
            
            # XMLを整形
            rough_string = ET.tostring(kml, 'unicode')
            reparsed = minidom.parseString(rough_string)
            return reparsed.toprettyxml(indent="  ")
            
        except Exception as e:
            st.error(f"KML作成エラー: {str(e)}")
            return None
    
    def _add_polygon_to_placemark(self, placemark, polygon):
        """PolygonをPlacemarkに追加"""
        multigeometry = placemark.find("MultiGeometry")
        if multigeometry is None:
            multigeometry = ET.SubElement(placemark, "MultiGeometry")
        
        kml_polygon = ET.SubElement(multigeometry, "Polygon")
        outer_boundary = ET.SubElement(kml_polygon, "outerBoundaryIs")
        linear_ring = ET.SubElement(outer_boundary, "LinearRing")
        coordinates = ET.SubElement(linear_ring, "coordinates")
        
        coord_str = ""
        for x, y in polygon.exterior.coords:
            coord_str += f"{x},{y},0 "
        coordinates.text = coord_str.strip()
    
    def extract_data(self, gdf, oaza, chome, koaza, chiban, range_m):
        """データ抽出処理"""
        try:
            # 検索条件構築
            search_condition = (
                (gdf['大字名'] == oaza) & 
                (gdf['地番'] == chiban) &
                (gdf['大字名'].notna()) &
                (gdf['地番'].notna())
            )
            
            if chome and chome != "選択なし" and '丁目名' in gdf.columns:
                search_condition = search_condition & (gdf['丁目名'] == chome) & (gdf['丁目名'].notna())
            
            if koaza and koaza != "選択なし" and '小字名' in gdf.columns:
                search_condition = search_condition & (gdf['小字名'] == koaza) & (gdf['小字名'].notna())
            
            df = gdf[search_condition]
            
            if df.empty:
                return None, None, f"該当する筆が見つかりませんでした"
            
            # 利用可能な列のみ選択
            available_columns = ["大字名", "地番", "geometry"]
            if "丁目名" in gdf.columns:
                available_columns.insert(1, "丁目名")
            if "小字名" in gdf.columns:
                insert_pos = 2 if "丁目名" in available_columns else 1
                available_columns.insert(insert_pos, "小字名")
            
            existing_columns = [col for col in available_columns if col in df.columns]
            df_summary = df.reindex(columns=existing_columns)
            
            # 中心点計算と周辺筆抽出
            cen = df_summary.geometry.centroid
            cen_gdf = gpd.GeoDataFrame(geometry=cen)
            cen_gdf['x'] = cen_gdf.geometry.x
            cen_gdf['y'] = cen_gdf.geometry.y
            
            # 検索範囲の計算
            x_center, y_center = cen_gdf['x'].iloc[0], cen_gdf['y'].iloc[0]
            
            # 4角ポイント
            points = pd.DataFrame([
                [y_center + range_m, x_center + range_m],  # top_right
                [y_center - range_m, x_center - range_m],  # lower_left
                [y_center + range_m, x_center - range_m],  # lower_right
                [y_center - range_m, x_center + range_m]   # top_left
            ], columns=["lon", "lat"])
            
            geometry = [Point(xy) for xy in zip(points.lat, points.lon)]
            four_points_gdf = gpd.GeoDataFrame(points, geometry=geometry)
            
            # 検索範囲のポリゴン作成
            sq = four_points_gdf.dissolve().convex_hull
            df1 = gpd.GeoDataFrame({'geometry': sq})
            df1 = df1.set_crs(gdf.crs)
            
            # 周辺筆抽出
            valid_data = gdf[(gdf['地番'].notna()) & (gdf['geometry'].notna())].copy()
            overlay_columns = ['地番', 'geometry']
            if '大字名' in valid_data.columns:
                overlay_columns.insert(0, '大字名')
            if '丁目名' in valid_data.columns:
                overlay_columns.insert(-1, '丁目名')
            if '小字名' in valid_data.columns:
                overlay_columns.insert(-1, '小字名')
            
            existing_overlay_columns = [col for col in overlay_columns if col in valid_data.columns]
            df2 = gpd.GeoDataFrame(valid_data[existing_overlay_columns])
            
            overlay_gdf = df1.overlay(df2, how='intersection')
            
            return df_summary, overlay_gdf, f"対象筆: {len(df_summary)}件, 周辺筆: {len(overlay_gdf)}件"
            
        except Exception as e:
            return None, None, f"エラー: {str(e)}"

def get_options(gdf, column, parent_column=None, parent_value=None):
    """指定された列の選択肢を取得（親列での絞り込み対応）"""
    try:
        if column not in gdf.columns:
            return None
        
        filtered_gdf = gdf[gdf[column].notna()]
        
        if parent_column and parent_value and parent_column in gdf.columns:
            filtered_gdf = filtered_gdf[
                (filtered_gdf[parent_column] == parent_value) & 
                (filtered_gdf[parent_column].notna())
            ]
        
        if len(filtered_gdf) == 0:
            return None
        
        return sorted(filtered_gdf[column].unique())
        
    except Exception as e:
        st.error(f"{column}取得エラー: {str(e)}")
        return None

def main():
    st.title("🗺️ 電子公図データ抽出ツール")
    st.markdown("---")
    
    extractor = KojiWebExtractor()
    
    # データソース選択
    st.header("📂 データソース選択")
    
    data_source = st.radio(
        "データソースを選択",
        ["📁 ローカルファイル", "🌐 Web URL", "🐙 GitHub"],
        horizontal=True
    )
    
    # データ読み込み処理
    data_loaded = False
    
    if data_source == "📁 ローカルファイル":
        uploaded_file = st.file_uploader(
            "SHPファイル（ZIP形式）をアップロード",
            type=['zip']
        )
        
        if uploaded_file is not None:
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    with zipfile.ZipFile(uploaded_file, 'r') as zip_ref:
                        zip_ref.extractall(temp_dir)
                    
                    shp_files = [f for f in os.listdir(temp_dir) if f.endswith('.shp')]
                    
                    if shp_files:
                        shp_path = os.path.join(temp_dir, shp_files[0])
                        st.session_state.gdf = gpd.read_file(shp_path)
                        data_loaded = True
                        st.success(f"✅ ファイル読み込み完了! ({len(st.session_state.gdf):,}件)")
                    else:
                        st.error("❌ SHPファイルが見つかりません")
                        
            except Exception as e:
                st.error(f"❌ ファイル読み込みエラー: {str(e)}")
    
    elif data_source == "🌐 Web URL":
        web_url = st.text_input(
            "ファイルのURL",
            placeholder="https://example.com/data.zip"
        )
        
        if st.button("🌐 URLから読み込み") and web_url:
            try:
                with st.spinner("URLからファイルを読み込み中..."):
                    st.session_state.gdf = extractor.load_shapefile_from_url(web_url)
                data_loaded = True
                st.success(f"✅ ファイル読み込み完了! ({len(st.session_state.gdf):,}件)")
            except Exception as e:
                st.error(f"❌ {str(e)}")
    
    elif data_source == "🐙 GitHub":
        col1, col2 = st.columns(2)
        with col1:
            github_owner = st.text_input("GitHubユーザー名")
            github_path = st.text_input("ファイルパス", placeholder="data/shapefile.zip")
        with col2:
            github_repo = st.text_input("リポジトリ名")
            github_branch = st.text_input("ブランチ名", value="main")
        
        if st.button("🐙 GitHubから読み込み") and all([github_owner, github_repo, github_path]):
            try:
                github_url = f"https://github.com/{github_owner}/{github_repo}/blob/{github_branch}/{github_path}"
                with st.spinner("GitHubからファイルを読み込み中..."):
                    st.session_state.gdf = extractor.load_shapefile_from_url(github_url)
                data_loaded = True
                st.success(f"✅ ファイル読み込み完了! ({len(st.session_state.gdf):,}件)")
            except Exception as e:
                st.error(f"❌ {str(e)}")
    
    # メイン機能
    if st.session_state.gdf is not None:
        st.markdown("---")
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.header("🔍 検索条件")
            
            # 大字名選択
            selected_oaza = None
            if '大字名' in st.session_state.gdf.columns:
                oaza_options = get_options(st.session_state.gdf, '大字名')
                if oaza_options:
                    selected_oaza = st.selectbox("大字名を選択", oaza_options)
                else:
                    st.error("❌ 大字名データがありません")
            else:
                st.error("❌ '大字名'列が見つかりません")
            
            # 丁目名選択
            selected_chome = None
            if selected_oaza:
                chome_options = get_options(st.session_state.gdf, '丁目名', '大字名', selected_oaza)
                if chome_options:
                    chome_list = ["選択なし"] + chome_options
                    selected_chome = st.selectbox("丁目名を選択（任意）", chome_list)
                else:
                    st.info("ℹ️ この大字名には丁目データがありません")
            
            # 小字名選択
            selected_koaza = None
            if selected_oaza:
                parent_col = '大字名'
                parent_val = selected_oaza
                if selected_chome and selected_chome != "選択なし":
                    # 丁目も考慮した小字取得（簡略化）
                    filtered_gdf = st.session_state.gdf[
                        (st.session_state.gdf['大字名'] == selected_oaza) &
                        (st.session_state.gdf['丁目名'] == selected_chome) &
                        (st.session_state.gdf['小字名'].notna())
                    ]
                else:
                    filtered_gdf = st.session_state.gdf[
                        (st.session_state.gdf['大字名'] == selected_oaza) &
                        (st.session_state.gdf['小字名'].notna())
                    ]
                
                if '小字名' in st.session_state.gdf.columns and len(filtered_gdf) > 0:
                    koaza_options = sorted(filtered_gdf['小字名'].unique())
                    koaza_list = ["選択なし"] + koaza_options
                    selected_koaza = st.selectbox("小字名を選択（任意）", koaza_list)
                else:
                    st.info("ℹ️ 小字データがありません")
            
            # 地番入力
            chiban = st.text_input("地番を入力", value="1174")
            
            # 検索範囲（固定）
            range_m = 61
            st.info(f"検索範囲: {range_m}m（固定）")
            
            # 抽出ボタン
            if st.button("🚀 データ抽出", type="primary", use_container_width=True):
                if selected_oaza and chiban:
                    with st.spinner("データ抽出中..."):
                        target_gdf, overlay_gdf, message = extractor.extract_data(
                            st.session_state.gdf, selected_oaza, selected_chome, selected_koaza, chiban, range_m
                        )
                    
                    st.info(message)
                    
                    if target_gdf is not None and overlay_gdf is not None:
                        st.session_state.target_gdf = target_gdf
                        st.session_state.overlay_gdf = overlay_gdf
                        
                        # ファイル名生成
                        file_name_parts = [selected_oaza]
                        if selected_chome and selected_chome != "選択なし":
                            file_name_parts.append(selected_chome)
                        if selected_koaza and selected_koaza != "選択なし":
                            file_name_parts.append(selected_koaza)
                        file_name_parts.append(chiban)
                        st.session_state.file_name = "_".join(file_name_parts)
                else:
                    st.error("大字名と地番を入力してください")
        
        with col2:
            st.header("📊 データ情報")
            
            # データサマリー
            if data_loaded or st.session_state.gdf is not None:
                with st.expander("ℹ️ データ情報", expanded=True):
                    st.write(f"**レコード数**: {len(st.session_state.gdf):,}件")
                    st.write(f"**座標系**: {st.session_state.gdf.crs}")
                    
                    if '丁目名' in st.session_state.gdf.columns:
                        chome_count = st.session_state.gdf['丁目名'].notna().sum()
                        st.write(f"**丁目データ**: {chome_count}件")
                    
                    if '小字名' in st.session_state.gdf.columns:
                        koaza_count = st.session_state.gdf['小字名'].notna().sum()
                        st.write(f"**小字データ**: {koaza_count}件")
            
            # 地番検索
            if st.checkbox("🔍 地番検索"):
                search_term = st.text_input("地番を検索")
                exact_match = st.checkbox("完全一致", value=False)
                
                if search_term and '地番' in st.session_state.gdf.columns:
                    chiban_str = st.session_state.gdf['地番'].astype(str)
                    
                    if exact_match:
                        filtered = st.session_state.gdf[
                            (chiban_str == search_term) & (chiban_str != 'nan')
                        ]
                    else:
                        filtered = st.session_state.gdf[
                            chiban_str.str.contains(search_term, na=False)
                        ]
                    
                    display_columns = [col for col in ['大字名', '丁目名', '小字名', '地番'] 
                                     if col in filtered.columns]
                    
                    if len(filtered) > 0:
                        st.write(f"**検索結果: {len(filtered)}件**")
                        st.dataframe(filtered[display_columns].head(20), use_container_width=True)
                    else:
                        st.info("該当する地番が見つかりませんでした")
        
        # 結果とダウンロード
        if 'target_gdf' in st.session_state and 'overlay_gdf' in st.session_state:
            st.markdown("---")
            st.header("📥 ダウンロード")
            
            col3, col4, col5 = st.columns(3)
            
            with col3:
                st.subheader("🎯 対象筆")
                target_kml = extractor.create_kml_from_geodataframe(
                    st.session_state.target_gdf, 
                    f"{st.session_state.file_name}_対象筆"
                )
                if target_kml:
                    st.download_button(
                        "📄 対象筆KML",
                        data=target_kml,
                        file_name=f"{st.session_state.file_name}_対象筆.kml",
                        mime="application/vnd.google-earth.kml+xml",
                        use_container_width=True
                    )
            
            with col4:
                st.subheader("🏘️ 周辺筆")
                overlay_kml = extractor.create_kml_from_geodataframe(
                    st.session_state.overlay_gdf,
                    f"{st.session_state.file_name}_周辺筆"
                )
                if overlay_kml:
                    st.download_button(
                        "📄 周辺筆KML",
                        data=overlay_kml,
                        file_name=f"{st.session_state.file_name}_周辺筆.kml",
                        mime="application/vnd.google-earth.kml+xml",
                        use_container_width=True
                    )
            
            with col5:
                st.subheader("📊 CSV出力")
                csv_data = st.session_state.overlay_gdf.copy()
                csv_data['中心X座標'] = csv_data['geometry'].centroid.x
                csv_data['中心Y座標'] = csv_data['geometry'].centroid.y
                csv_export = csv_data.drop(columns=['geometry']).to_csv(index=False)
                
                st.download_button(
                    "📊 周辺筆CSV",
                    data=csv_export,
                    file_name=f"{st.session_state.file_name}_周辺筆.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            
            # 結果プレビュー
            st.markdown("---")
            st.header("👀 結果プレビュー")
            
            tab1, tab2 = st.tabs(["対象筆", "周辺筆"])
            
            with tab1:
                if not st.session_state.target_gdf.empty:
                    display_df = st.session_state.target_gdf.drop(columns=['geometry'])
                    st.dataframe(display_df, use_container_width=True)
            
            with tab2:
                if not st.session_state.overlay_gdf.empty:
                    st.write(f"**周辺筆一覧 ({len(st.session_state.overlay_gdf)}件)**")
                    display_df = st.session_state.overlay_gdf.drop(columns=['geometry'])
                    st.dataframe(display_df, use_container_width=True)
    
    else:
        st.info("👆 データソースを選択してファイルを読み込んでください")

if __name__ == "__main__":
    main()