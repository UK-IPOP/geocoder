from __future__ import annotations

import os
import re

import dotenv
import pandas as pd
from arcgis.geocoding import geocode
from arcgis.gis import GIS
from rich.progress import track


def initialize():
    dotenv.load_dotenv()
    GIS(
        api_key=os.getenv("ARCGIS_API_KEY"),
    )


def load_case_file() -> pd.DataFrame:
    df = pd.read_csv("data/raw/cases.csv", low_memory=False)
    return df


def prepare_df(df: pd.DataFrame) -> pd.DataFrame:
    addresses = df.apply(lambda row: create_address(row, flag="incident"), axis=1)
    df["incident_address"] = [a[0] for a in addresses]
    df["incident_address_sub"] = [a[1] for a in addresses]
    return df


def run_geocoding(addresses: list[str]) -> list[dict[str, str | float]]:
    search_bounds = {
        "xmin": -87.38,
        "ymin": 36.96,
        "xmax": -91.60,
        "ymax": 42.57,
        "spatialReference": {"wkid": 4326},
    }
    results = []
    for address in track(addresses, description="Geocoding..."):
        if pd.isna(address) or "unknown" in address:
            results.append(
                {"address": address, "latitude": None, "longitude": None, "score": None}
            )
            continue
        geocoded_info = geocode(
            address, search_extent=search_bounds, location_type="rooftop"
        )
        if geocoded_info:
            best_result = geocoded_info[0]
            geo_data = {
                "address": best_result["address"],
                "latitude": best_result["location"]["y"],
                "longitude": best_result["location"]["x"],
                "score": best_result["score"],
            }
            results.append(geo_data)
        else:
            results.append(
                {"address": address, "latitude": None, "longitude": None, "score": None}
            )
    return results


def remove_apartment_info(x: str) -> str:
    """Uses regex to remove # and Apt from Address

    Args:
        x (str): Address

    Returns:
        str: Cleaned address
    """
    # regex 1 to look for apartments and #s
    result1 = re.sub(r"apt.*|\#.*|.*nh,", "", x)
    # regex 2 to specify only alphanumeric + '.' for abbreviations and spaces
    result2 = re.sub(r"[^a-zA-Z0-9.\s]", "", result1)
    return result2


def clean_address(street: str) -> str | None:
    """Cleans an address by calling other utility functions.

    Args:
        street (str): street address

    Returns:
        Union[str, None]: cleaned address or None
    """
    # handles 'unknown' and variations
    if pd.isna(street):
        return None
    s = street.lower().strip()
    if "unk" in s or "n/a" in s or s == "same" or s == "none":
        return None
    no_apartment_info = remove_apartment_info(s)
    return no_apartment_info


def city_sub(row: pd.Series) -> tuple[str, bool]:
    """Identifies whether a city substitution can be used.

    This function handles cases where the incident_city is null
    and it looks for a city in residence_city.  If there is one,
    it subsitutes the latter for the former.

    Args:
        row (pd.Series): row in a dataframe

    Returns:
        tuple[str, bool]: a tuple containing the city and whether it was subsituted
    """
    if pd.notna(row["incident_city"]):
        city = row["incident_city"].title().strip()
        subbed = False
    elif pd.isna(row["incident_city"]) and pd.notna(row["residence_city"]):
        city = row["residence_city"].title().strip()
        subbed = True
    else:
        city = ""
        subbed = False
    return city, subbed


def create_address(row: pd.Series, flag: str) -> tuple[str, bool]:
    """Creates the address field column for each row of a dataframe.

    Args:
        row (pd.Series): row in a dataframe.
        flag (str): flag to whether to perform on incident or death

    Returns:
        tuple[str, bool]: cleaned address and whether a city substitution was performed.
    """
    if flag == "incident":
        street = (
            clean_address(row["incident_street"])
            if pd.notna(row["incident_street"])
            else ""
        )
        city, subbed = city_sub(row)
        zip_code = "" if pd.isna(row["incident_zip"]) else row["incident_zip"]
        address = f"{street if street else ''} {city if city else ''} {zip_code if zip_code else ''}".upper().strip()
        return address, subbed
    elif flag == "death":
        street = (
            clean_address(row["death_street"]) if pd.notna(row["death_street"]) else ""
        )
        city = row["death_city"].title().strip()
        state = (
            row["death_state"].title().strip() if pd.notna(row["death_state"]) else ""
        )
        zip_code = "" if pd.isna(row["death_zip"]) else row["death_zip"]
        address = f"{street if street else ''} {city if city else ''} {state if state else ''} {zip_code if zip_code else ''}".upper().strip()
        return address, False
    else:
        raise ValueError("flag must be either 'incident' or 'death'")


def composite_lat_long(row: pd.Series, toggle: str) -> float | None:
    if toggle == "lat":
        if pd.notna(row["latitude"]):
            return row["latitude"]
        elif pd.notna(row["geocoded_latitude"]):
            return row["geocoded_latitude"]
        else:
            return None
    elif toggle == "long":
        if pd.notna(row["longitude"]):
            return row["longitude"]
        elif pd.notna(row["geocoded_longitude"]):
            return row["geocoded_longitude"]
        else:
            return None
    else:
        raise Exception("expected toggle value to be either `lat` or `long`")


def combine_geo_results(
    coded_df: pd.DataFrame, original_df: pd.DataFrame
) -> pd.DataFrame:
    df = pd.merge(
        left=original_df,
        right=coded_df[
            [
                "casenumber",
                "geocoded_latitude",
                "geocoded_longitude",
                "geocoded_score",
                "geocoded_address",
            ]
        ],
        on="casenumber",
        how="left",
    )
    df["recovered"] = df.geocoded_score.apply(lambda x: 1 if pd.notna(x) else 0)
    df["final_latitude"] = df.apply(lambda row: composite_lat_long(row, "lat"), axis=1)
    df["final_longitude"] = df.apply(
        lambda row: composite_lat_long(row, "long"), axis=1
    )
    return df


def main():
    cases = load_case_file()
    no_geo = cases[(pd.isna(cases["latitude"])) | (pd.isna(cases["longitude"]))]
    # no_geo = no_geo.loc[:50]

    # preprocess
    no_geo = prepare_fixed_df(no_geo)
    geocoding_results = run_geocoding(no_geo.full_address.values)
    # assign results to dataframe
    no_geo["geocoded_latitude"] = [x["latitude"] for x in geocoding_results]
    no_geo["geocoded_longitude"] = [x["longitude"] for x in geocoding_results]
    no_geo["geocoded_score"] = [x["score"] for x in geocoding_results]
    no_geo["geocoded_address"] = [x["address"] for x in geocoding_results]
    recovered_df = combine_geo_results(no_geo, cases)
    # save results
    recovered_df.to_csv("data/processed/recovered_lat_long.csv", index=False)


if __name__ == "__main__":
    initialize()
    main()
