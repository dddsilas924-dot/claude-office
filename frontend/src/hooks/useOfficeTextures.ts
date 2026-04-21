/**
 * Hook for loading all office sprite textures.
 *
 * Centralizes texture loading logic and provides a clean interface
 * for accessing loaded textures throughout the office game.
 */

import { useState, useEffect } from "react";
import { Assets, Texture } from "pixi.js";

export interface OfficeTextures {
  keyboard: Texture | null;
  headset: Texture | null;
  sunglasses: Texture | null;
}

interface UseOfficeTexturesResult {
  textures: OfficeTextures;
  loaded: boolean;
}

const TEXTURE_PATHS: Record<keyof OfficeTextures, string> = {
  keyboard: "/sprites/keyboard_back.png",
  headset: "/sprites/headset_small.png",
  sunglasses: "/sprites/sunglasses.png",
};

const EMPTY_TEXTURES: OfficeTextures = {
  keyboard: null,
  headset: null,
  sunglasses: null,
};

/**
 * Hook to load all office sprite textures.
 * Returns textures object and loaded state.
 */
export function useOfficeTextures(): UseOfficeTexturesResult {
  const [textures, setTextures] = useState<OfficeTextures>(EMPTY_TEXTURES);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const loadTextures = async () => {
      try {
        const keys = Object.keys(TEXTURE_PATHS) as (keyof OfficeTextures)[];
        const paths = keys.map((key) => TEXTURE_PATHS[key]);

        const loadedTextures = await Promise.all(
          paths.map((path) => Assets.load(path)),
        );

        const textureMap = keys.reduce(
          (acc, key, index) => {
            acc[key] = loadedTextures[index];
            return acc;
          },
          {} as Record<keyof OfficeTextures, Texture>,
        );

        setTextures(textureMap as OfficeTextures);
        setLoaded(true);
      } catch {
        // Still mark as loaded to show fallback graphics
        setLoaded(true);
      }
    };

    loadTextures();
  }, []);

  return { textures, loaded };
}
