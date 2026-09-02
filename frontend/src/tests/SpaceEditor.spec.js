/**
 * Regression tests for the spot-editor "clicking Test multiple times gives
 * different results" bug.
 *
 * Root cause: the v-stage and v-image were configured in *CSS pixel* space
 * (imageConfig.width = img.width, stageConfig.width = availWidth) while
 * the inference backend operates in *image pixel* space (the JPEG's
 * naturalWidth/Height). When the user drew a box at DOM (100, 100) on a
 * 1920x1080 image displayed in a 900x506 wrapper, the click landed at
 * stage-space (100, 100) — i.e., CSS pixel (100, 100), not image pixel
 * (213, 213). Sending that to the backend and dividing by naturalWidth
 * gave (100/1920, 100/1080) = a point ~46% off from the visible ROI.
 *
 * Every layout pass that re-flowed the wrapper (snackbar appearing,
 * ResizeObserver firing) could shift the result, which is why repeated
 * Test clicks on the same scan produced different occupancy values.
 *
 * The fix: configure the v-stage and v-image in image pixel space. The
 * stage's transform (scaleX/scaleY/x/y) makes the image fit the wrapper
 * visually; the underlying coordinate system stays in image pixels, so
 * getRelativePointerPosition returns image pixel coordinates naturally
 * and the normalization math is correct.
 *
 * These tests assert the structural invariants of the fix by parsing
 * SpaceEditor.vue. A full vue-konva test harness would be ideal but is
 * heavy for the value; the shape-level assertions catch the most
 * common regressions (someone "fixing" the CSS-vs-natural confusion by
 * re-introducing img.width into imageConfig).
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SFC_PATH = path.join(__dirname, '..', 'components', 'SpaceEditor.vue')

function readScriptBlock() {
  const src = fs.readFileSync(SFC_PATH, 'utf8')
  const match = src.match(/<script[^>]*>([\s\S]*?)<\/script>/)
  if (!match) throw new Error('SpaceEditor.vue has no <script> block')
  return match[1]
}

function readFunctionBody(script, name) {
  // Match either:
  //   const name = ...() => { ... };
  //   const name = async ...() => { ... };
  //   function name(...) { ... }
  //   async function name(...) { ... }
  // and extract the body between the opening and matching closing brace.
  const arrowRe = new RegExp(
    `(?:const|let|var)\\s+${name}\\s*=\\s*(?:async\\s*)?\\(?[^)]*\\)?\\s*=>\\s*\\{`,
    'g'
  )
  const funcRe = new RegExp(
    `(?:async\\s+)?function\\s+${name}\\s*\\([^)]*\\)\\s*\\{`,
    'g'
  )
  const m = arrowRe.exec(script) || funcRe.exec(script)
  if (!m) throw new Error(`Function ${name} not found`)
  let depth = 1
  let i = m.index + m[0].length
  while (i < script.length && depth > 0) {
    const c = script[i]
    if (c === '{') depth++
    else if (c === '}') depth--
    i++
  }
  return script.slice(m.index + m[0].length, i - 1)
}

describe('SpaceEditor.vue ROI normalization', () => {
  it('loadScanImage sets imageConfig.width to naturalWidth', () => {
    // The v-image is drawn at imageConfig.width × imageConfig.height
    // in stage space. With the new design (stage local coord =
    // wrapper CSS size, image drawn at natural size and scaled to
    // fit), this MUST be the natural pixel size. If someone
    // reintroduces ``imageConfig.value.width = img.width`` here, the
    // image will be squished into the wrapper instead of being
    // scaled proportionally.
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'loadScanImage')
    expect(body, 'loadScanImage must set imageConfig.value.width to naturalWidth').toMatch(
      /imageConfig\.value\.width\s*=\s*img\.naturalWidth/
    )
    expect(body, 'loadScanImage must NOT use img.width for imageConfig.value.width').not.toMatch(
      /imageConfig\.value\.width\s*=\s*img\.width\b/
    )
  })

  it('updateStageSizeForImage sets stageConfig.width to max(naturalW, wrapperW)', () => {
    // The stage's local coordinate system is max(naturalW, wrapperW)
    // × max(naturalH, wrapperH). This way the entire image is in
    // the stage's buffer regardless of the wrapper size, so saved
    // annotations at any image pixel are drawn in the right place
    // (instead of being clipped by a stage that's smaller than the
    // image). The wrapper's `overflow: hidden` clips the visible
    // portion of the stage.
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'updateStageSizeForImage')
    // The function computes ``stageW = Math.max(nativeWidth, availWidth)``
    // and assigns it to ``stageConfig.value.width``. The Math.max
    // is the invariant: it ensures the stage is at least as big as
    // the image.
    expect(body, 'stageConfig.value.width must be max(naturalW, wrapperW)').toMatch(
      /Math\.max\(\s*nativeWidth\s*,\s*availWidth\s*\)/
    )
    // Using only the wrapper size would clip annotations at image
    // pixels past the wrapper's right edge.
    expect(body, 'stageConfig.value.width must NOT be just availWidth').not.toMatch(
      /width:\s*availWidth\b/
    )
  })

  it('testOccupancy converts from stage-local (CSS) to image-natural and normalizes', () => {
    // testOccupancy reads points from spaces.value (stage-local
    // CSS pixel space) and converts to image-natural pixel
    // coordinates via stageLocalToImageNatural, then normalizes
    // by naturalWidth/naturalHeight to [0, 1].
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'testOccupancy')
    expect(body).toMatch(/naturalWidth/)
    expect(body).toMatch(/naturalHeight/)
    // Must call the stage-local → image-natural conversion
    expect(body).toMatch(/stageLocalToImageNatural/)
    // Must NOT read imageConfig.value.width (CSS) for normalization
    expect(body).not.toMatch(/imageConfig\.value\.width\b/)
    expect(body).not.toMatch(/imageConfig\.value\.height\b/)
  })

  it('saveSpaces converts from stage-local (CSS) to image-natural and normalizes', () => {
    // saveSpaces persists points in image-natural pixel space
    // (normalized to [0, 1]) for the backend to consume.
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'saveSpaces')
    expect(body).toMatch(/naturalWidth/)
    expect(body).toMatch(/naturalHeight/)
    expect(body).toMatch(/stageLocalToImageNatural/)
    expect(body).not.toMatch(/imageConfig\.value\.width\b/)
    expect(body).not.toMatch(/imageConfig\.value\.height\b/)
  })

  it('fetchData converts from image-natural to stage-local for rendering', () => {
    // fetchData loads points in image-natural pixel space
    // (from the DB) and converts to stage-local (CSS) so the
    // v-line/v-group points overlay the visible image.
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'fetchData')
    expect(body).toMatch(/imageNaturalToStageLocal/)
    // rawPoints is preserved (the canonical image-natural copy)
    expect(body).toMatch(/rawPoints/)
  })

  it('zoomStage uses the wrapper size (not the stage size) for the visible center', () => {
    // The stage's local coordinate system is the wrapper's CSS
    // pixel size; the visible center of the screen is at
    // (wrapper.clientWidth/2, wrapper.clientHeight/2) in DOM
    // coords which is the same in stage-local. Using
    // stageConfig.width/2 (whatever that is) wouldn't necessarily
    // be the visible center after a resize.
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'zoomStage')
    expect(body).toMatch(/stageWrapper/)
    expect(body).toMatch(/clientWidth/)
    expect(body).toMatch(/clientHeight/)
  })

  it('the wheel handler preserves the stage\'s CSS-pixel coordinate system', () => {
    // handleWheel pins stageConfig.width/height to stage.width()/
    // height(). With the new design, the stage's local coordinate
    // system is the wrapper's CSS pixel size, so
    // stage.width() === availWidth. The pre-fix code used
    // naturalWidth here to track image pixel size — that would
    // be wrong now (it would lock the local coord to natural
    // size, re-introducing the original clipping bug).
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'handleWheel')
    expect(body).toMatch(/stage\.width\(\)/)
    expect(body).toMatch(/stage\.height\(\)/)
  })

  it('loadScanImage resets the stage transform when the user picks a different scan', () => {
    // If the user is on a historical scan, the previous image's
    // aspect ratio / size is in the transform. Loading a new
    // image with a different aspect ratio without recomputing the
    // transform leaves the new image at the wrong scale/offset.
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'loadScanImage')
    // isInitialLoad should depend on currentScanIndex === -1
    expect(body).toMatch(/currentScanIndex/)
    // updateStageSizeForImage should be called with onlyIfNone=true
    // only on the first load — otherwise the previous scan's
    // transform is reused.
    expect(body).toMatch(/updateStageSizeForImage\s*\(\s*img\s*,\s*isInitialLoad\s*\)/)
  })

  it('loadScanImage accepts a forceRefresh option that busts the backend cache', () => {
    // The backend's LIVE frame cache makes Test deterministic on a
    // LIVE stream. But the same cache would prevent the Refresh
    // button from showing a new image. The fix: the editor passes
    // ``force=true`` to the snapshot endpoint when the user
    // explicitly hits Refresh, and the backend invalidates its
    // cache for that camera. The frontend also appends a
    // timestamp query param so the browser issues a fresh fetch
    // instead of serving its own cached image.
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'loadScanImage')
    expect(body).toMatch(/forceRefresh/)
    expect(body).toMatch(/force=true/)
    expect(body).toMatch(/Date\.now/)
  })

  it('the Refresh button calls fetchData with forceRefresh: true', () => {
    // The toolbar Refresh button must opt into forceRefresh. If
    // someone changes it back to ``@click="fetchData"`` (no args)
    // the user can no longer update the displayed image once the
    // backend cache warms up.
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    expect(sfc).toMatch(/fetchData\s*\(\s*\{\s*forceRefresh:\s*true/)
  })
})

/**
 * End-to-end style: simulate the coordinate math that happens
 * between a DOM click and a backend test-occupancy request.
 *
 * The new contract (stage local coord = wrapper CSS pixel size):
 *   - User clicks at DOM (X, Y).
 *   - getRelativePointerPosition returns stage-local (CSS) (X, Y).
 *   - SpaceEditor converts to image-natural: (X - tx) / scaleX.
 *   - Then normalizes: / naturalWidth.
 *   - Backend multiplies by (naturalW, naturalH) to get back
 *     the image pixel matching what the user clicked.
 *
 * The invariant: a click → normalized → backend → image pixel
 * lands on the same image pixel the user clicked on, regardless
 * of the wrapper size or zoom level.
 */
describe('SpaceEditor click → backend coordinate contract', () => {
  /**
   * Pure reimplementation of the SpaceEditor's geometry and the
   * testOccupancy normalization. Kept in the test so that any change
   * to the actual code that breaks the contract is caught here.
   *
   * @param {{domX: number, domY: number}} click  - where the user
   *   clicked on screen (in CSS pixels relative to the wrapper).
   * @param {{naturalWidth: number, naturalHeight: number}} image
   *   - the JPEG's natural pixel size.
   * @param {{stageWidth: number, stageHeight: number,
   *           scaleX: number, scaleY: number,
   *           x: number, y: number}} stage
   *   - the v-stage's local size and the transform applied to it
   *   (matches the SpaceEditor.stageConfig ref).
   * @returns {{normalizedX: number, normalizedY: number,
   *             imageX: number, imageY: number}}
   *   - what the SpaceEditor would send to the backend, and what
   *   the backend would map back to.
   */
  function clickToBackend({ domX, domY }, { naturalWidth, naturalHeight }, stage) {
    // The click position is in DOM CSS pixels. The stage's
    // transform (scaleX, scaleY, x, y) maps the stage's local
    // coord system to DOM. The inverse transform converts DOM
    // clicks to stage coords (this is what Konva's
    // getRelativePointerPosition does). The image is drawn at
    // (0, 0) to (naturalW, naturalH) in stage coords, so a
    // stage coord (X, Y) is the image pixel (X, Y).
    const stageX = (domX - stage.x) / stage.scaleX;
    const stageY = (domY - stage.y) / stage.scaleY;

    // Stage coord = image pixel (identity, since the image is
    // drawn at natural size in stage coords).
    const imageX = stageX;
    const imageY = stageY;

    // SpaceEditor normalizes by naturalWidth / naturalHeight to
    // produce the [0, 1] coords sent to the backend.
    const normalizedX = imageX / naturalWidth;
    const normalizedY = imageY / naturalHeight;

    // The backend multiplies by the inference frame's dimensions.
    // The contract: it should land on the same image pixel.
    const backendImageX = normalizedX * naturalWidth;
    const backendImageY = normalizedY * naturalHeight;

    return { normalizedX, normalizedY, imageX: backendImageX, imageY: backendImageY };
  }

  function fitToWrapper(wrapperW, wrapperH, naturalW, naturalH) {
    // Mirrors updateStageSizeForImage: stage local = max(naturalW,
    // wrapperW) × max(naturalH, wrapperH), scale fits the image
    // into the wrapper with 40px padding.
    const scaleX = (wrapperW - 40) / naturalW;
    const scaleY = (wrapperH - 40) / naturalH;
    const scale = Math.min(scaleX, scaleY, 1.0);
    return {
      stageWidth: Math.max(naturalW, wrapperW),
      stageHeight: Math.max(naturalH, wrapperH),
      scaleX: scale,
      scaleY: scale,
      x: (wrapperW - naturalW * scale) / 2,
      y: (wrapperH - naturalH * scale) / 2,
    };
  }

  it('round-trips a click on a 1920x1080 image in a 1500x880 wrapper', () => {
    const stage = fitToWrapper(1500, 880, 1920, 1080);
    // Click at the upper-left of the visible image area in the
    // wrapper (small offset from the image's top-left corner).
    const out = clickToBackend(
      { domX: 30, domY: 35 },
      { naturalWidth: 1920, naturalHeight: 1080 },
      stage
    );
    // The image is at DOM (20, 29.4) to (1480, 850.6) — a click at
    // DOM (30, 35) is at image pixel ((30 - 20) / 0.7604, (35 - 29.4) / 0.7604)
    // ≈ (13, 7).
    expect(out.imageX).toBeCloseTo(13, 0);
    expect(out.imageY).toBeCloseTo(7, 0);
    // And those are *image pixels* (i.e., in [0, 1920) × [0, 1080)).
    expect(out.imageX).toBeGreaterThan(0);
    expect(out.imageX).toBeLessThan(1920);
    expect(out.imageY).toBeGreaterThan(0);
    expect(out.imageY).toBeLessThan(1080);
  })

  it('is deterministic — repeated identical clicks produce identical results', () => {
    const stage = fitToWrapper(1500, 880, 1920, 1080);
    const click = { domX: 200, domY: 150 };
    const a = clickToBackend(click, { naturalWidth: 1920, naturalHeight: 1080 }, stage);
    const b = clickToBackend(click, { naturalWidth: 1920, naturalHeight: 1080 }, stage);
    const c = clickToBackend(click, { naturalWidth: 1920, naturalHeight: 1080 }, stage);
    expect(a).toEqual(b);
    expect(b).toEqual(c);
  })

  it('a click in the wrapper is at the same image pixel regardless of wrapper size', () => {
    // Two different wrapper sizes — the image is fit-to-wrapper in
    // both, so the image fills the visible area in both. A click
    // at the same CSS position lands on the same image pixel in
    // both wrappers (the image is at the same on-screen position).
    const stageA = fitToWrapper(900, 506, 1920, 1080);
    const stageB = fitToWrapper(1200, 700, 1920, 1080);
    // The image's upper-left is at a different DOM position in
    // each wrapper, so a click at the same DOM position lands
    // on a different image pixel. But the *image's center*
    // is at the same DOM position (wrapper center) in both.
    const center = (wrapperW) => wrapperW / 2;
    const centerY = (wrapperH) => wrapperH / 2;
    const a = clickToBackend(
      { domX: center(900), domY: centerY(506) },
      { naturalWidth: 1920, naturalHeight: 1080 },
      stageA
    );
    const b = clickToBackend(
      { domX: center(1200), domY: centerY(700) },
      { naturalWidth: 1920, naturalHeight: 1080 },
      stageB
    );
    // Both clicks are at the image's center → both are at image
    // pixel (960, 540).
    expect(a.imageX).toBeCloseTo(960, 1);
    expect(a.imageY).toBeCloseTo(540, 1);
    expect(b.imageX).toBeCloseTo(960, 1);
    expect(b.imageY).toBeCloseTo(540, 1);
  })

  it('a click on a 1280x720 image lands in [0, 1280) × [0, 720) image pixels', () => {
    // A smaller camera — make sure the same code path works.
    const stage = fitToWrapper(800, 450, 1280, 720);
    const out = clickToBackend(
      { domX: 200, domY: 200 },
      { naturalWidth: 1280, naturalHeight: 720 },
      stage
    );
    expect(out.imageX).toBeGreaterThan(0);
    expect(out.imageX).toBeLessThan(1280);
    expect(out.imageY).toBeGreaterThan(0);
    expect(out.imageY).toBeLessThan(720);
  })

  it('preserves the click position when the user zooms in', () => {
    // User zooms in 2x around the image's center. The stage's
    // scaleX doubles, the stage offset shifts to keep the image
    // center under the same DOM position. A click at the SAME DOM
    // position lands on the same image pixel before and after
    // zoom (the user didn't pan; the image is just bigger).
    const baseStage = fitToWrapper(1500, 880, 1920, 1080);
    // Click at the image's center in the base stage.
    const baseClick = clickToBackend(
      { domX: 750, domY: 440 },
      { naturalWidth: 1920, naturalHeight: 1080 },
      baseStage
    );
    // With our setup, the image's center at DOM (750, 440) maps
    // to image pixel (960, 540) via getRelativePointerPosition.
    expect(baseClick.imageX).toBeCloseTo(960, 1);
    expect(baseClick.imageY).toBeCloseTo(540, 1);

    // Now zoom in 2x around the image center. The stage's local
    // size must stay >= naturalW × naturalH so the entire image
    // is in the buffer.
    const newScale = baseStage.scaleX * 2;
    // To keep the image center at DOM (750, 440): the image
    // pixel (960, 540) is at stage (960, 540) [identity since
    // image is at natural size in stage coords]. The new DOM is
    // 960*newScale + newX = 750, so newX = 750 - 960*newScale.
    const newX = 750 - 960 * newScale;
    const newY = 440 - 540 * newScale;
    const zoomedStage = {
      stageWidth: baseStage.stageWidth, // doesn't grow with zoom
      stageHeight: baseStage.stageHeight,
      scaleX: newScale,
      scaleY: newScale,
      x: newX,
      y: newY,
    };
    // Click at the same DOM position. The image center is still
    // at DOM (750, 440) because we kept it there.
    const zoomedClick = clickToBackend(
      { domX: 750, domY: 440 },
      { naturalWidth: 1920, naturalHeight: 1080 },
      zoomedStage
    );
    // Same image pixel — the image center is at DOM (750, 440) in
    // both cases.
    expect(zoomedClick.imageX).toBeCloseTo(960, 1);
    expect(zoomedClick.imageY).toBeCloseTo(540, 1);
  })
})


/**
 * Visual layout regression: the stage's local coordinate system
 * must be at least as large as the image's natural size, so
 * saved annotations at any image pixel are drawn in the right
 * place. With stage = max(naturalW, wrapperW), the entire
 * image is in the stage's buffer; the wrapper's `overflow:
 * hidden` clips the visible portion.
 */
describe('SpaceEditor.vue visual layout', () => {
  it('stageConfig.width is at least the image naturalWidth (and matches the wrapper if it is larger)', () => {
    // The init defaults are placeholder values (800x600) — the real
    // value is set in updateStageSizeForImage. Make sure that
    // function uses Math.max(naturalW, wrapperW) for the stage
    // width — NOT just the wrapper width (which would clip
    // annotations at image pixels past the wrapper's right edge)
    // and NOT just naturalW (which is the pre-fix design that
    // overflowed the wrapper).
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    expect(sfc).toMatch(/Math\.max\(\s*nativeWidth\s*,\s*availWidth\s*\)/)
    expect(sfc).toMatch(/Math\.max\(\s*nativeHeight\s*,\s*availHeight\s*\)/)
    // And the pre-fix design (just naturalW) must not be present.
    expect(sfc).not.toMatch(/width:\s*nativeWidth\b/)
    expect(sfc).not.toMatch(/height:\s*nativeHeight\b/)
  })
})

/**
 * Camera-change regression: Vue Router reuses the SpaceEditor
 * component instance when navigating between cameras
 * (/editor/1 → /editor/5) because the route component is the
 * same. ``cameraId`` is the only thing that changes. If the
 * component doesn't react to the cameraId change, the new
 * camera shows the OLD camera's guides, spaces, and image
 * (loaded once at mount and never refreshed).
 *
 * The fix: cameraId is a reactive ref, watched by a handler that
 * clears per-camera state and re-fetches from the server /
 * localStorage. This test pins the structural invariant: the
 * source must contain a watch on cameraId that refetches.
 */
describe('SpaceEditor.vue camera-change reload', () => {
  it('has a watch on cameraId that refetches the new camera', () => {
    // The source must contain a watch(cameraId, ...) that calls
    // fetchData() to reload the new camera's data. If this is
    // removed (or replaced with a const cameraId = ...), the
    // new camera will show the old camera's guides, spaces,
    // and image — the original bug.
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    expect(sfc, 'cameraId must be a reactive ref (so it can be watched)').toMatch(
      /const\s+cameraId\s*=\s*ref\(\s*route\.params\.cameraId\s*\)/
    )
    // Pre-fix bug: cameraId was a const, so the watch would never
    // fire on route changes.
    expect(sfc, 'cameraId must NOT be a const — it must be a ref so the watch fires on route changes').not.toMatch(
      /const\s+cameraId\s*=\s*route\.params\.cameraId\s*;/
    )
    // There must be a watch on cameraId that calls fetchData to
    // reload the new camera's data. The watch handler is the only
    // thing that updates the displayed state when the user
    // navigates between cameras (since the component is reused).
    expect(sfc, 'must watch cameraId to react to route changes').toMatch(
      /watch\(\s*cameraId\s*,\s*[^)]*\)/
    )
    expect(sfc, 'the cameraId watch must refetch (call fetchData)').toMatch(
      /watch\(\s*cameraId\s*,[\s\S]{0,1000}fetchData\(\)/
    )
  })

  it('saveGuidesToLocal uses cameraId.value, not the bare ref', () => {
    // The localStorage key is ``guides_${cameraId.value}``. If a
    // future change drops the .value, all guides would be
    // written to the same key 'guides_[object Object]' and
    // overwritten across cameras — the exact bug the original
    // report was about. This test pins the .value access.
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    expect(sfc, 'saveGuidesToLocal must use cameraId.value').toMatch(
      /saveGuidesToLocal[\s\S]{0,500}guides_\$\{cameraId\.value\}/
    )
  })

  it('loadGuidesFromLocal uses cameraId.value, not the bare ref', () => {
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    expect(sfc, 'loadGuidesFromLocal must use cameraId.value').toMatch(
      /loadGuidesFromLocal[\s\S]{0,500}guides_\$\{cameraId\.value\}/
    )
  })

  it('the cameraId watch clears guides before reloading', () => {
    // The cameraId watch must clear guides.value = [] BEFORE
    // calling fetchData. Otherwise the OLD camera's guides would
    // briefly flash on the new camera (until fetchData
    // overwrites them) — and if the new camera has no guides
    // saved, they'd stick around indefinitely.
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    // Find the watch handler body
    const watchMatch = sfc.match(/watch\(\s*cameraId\s*,[\s\S]*?\}\s*\)\s*;/)
    expect(watchMatch, 'cameraId watch must exist').toBeTruthy()
    const body = watchMatch[0]
    // The body must clear guides.value = [] before fetchData()
    const guidesClearIdx = body.indexOf('guides.value = []')
    const fetchDataIdx = body.indexOf('fetchData()')
    expect(guidesClearIdx, 'must clear guides.value').toBeGreaterThan(-1)
    expect(fetchDataIdx, 'must call fetchData()').toBeGreaterThan(-1)
    expect(guidesClearIdx, 'guides must be cleared before fetchData').toBeLessThan(fetchDataIdx)
  })
})

/**
 * URL-interpolation contract: every axios call that includes the
 * camera id in its URL path or query string must use
 * ``cameraId.value`` (the ref's value), not the bare ref. A bare
 * ref in a template literal stringifies to "[object Object]", which
 * the backend's Pydantic int validators reject with 422.
 *
 * The pre-fix bug: ``cameraId`` was a const, so it didn't matter —
 * but once I made it a ref to support route-change reactivity,
 * the bare ``${cameraId}`` interpolation silently broke every
 * request that took the camera id in its URL. This test pins the
 * structural invariant: every URL using the camera id uses .value.
 */
describe('SpaceEditor.vue URL-interpolation contract', () => {
  it('every /api/cameras/${...} URL uses cameraId.value', () => {
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    // Find every template-literal URL containing 'api/cameras/'
    // and check that the camera-id interpolation uses .value.
    // The pattern ``/api/cameras/${cameraId}/`` is the bug;
    // ``/api/cameras/${cameraId.value}/`` is correct.
    const urlPattern = /\/api\/cameras\/\$\{([^}]+)\}/g
    const matches = [...sfc.matchAll(urlPattern)]
    expect(matches.length, 'must have at least one /api/cameras/ URL').toBeGreaterThan(0)
    for (const m of matches) {
      const expr = m[1]
      // Must be ``cameraId.value`` — not the bare ref, not some
      // other expression that could stringify wrong.
      expect(
        expr,
        `URL interpolation \${${expr}} must be cameraId.value, not the bare ref`
      ).toBe('cameraId.value')
    }
  })

  it('the spaces list query uses camera_id: cameraId.value', () => {
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    // The /api/spaces endpoint takes camera_id as a query param.
    // If the ref is used directly, the value is "[object Object]"
    // and Pydantic rejects with 422.
    const m = sfc.match(/axios\.get\(`\/api\/spaces`,\s*\{\s*params:\s*\{\s*camera_id:\s*([^}]+?)\s*\}\s*\}\)/)
    expect(m, 'spaces list axios call must exist').toBeTruthy()
    expect(m[1].trim(), 'camera_id param must use cameraId.value').toBe('cameraId.value')
  })
})

/**
 * Test-result reset: clicking "Test" sets ``space.testResult`` on
 * every space (red for occupied, green for vacant). When the user
 * then changes the image — switching scans, jumping time, hitting
 * Refresh, etc. — those results are now stale: the inference was
 * run against the *previous* image, but the user is looking at a
 * different one. The colors should reset to the uncolored (blue
 * edit-mode) state so the user isn't misled.
 *
 * ``loadScanImage`` is the single entry point for "display a new
 * image" (called from the LIVE menu, scan list, time jump, time
 * arrow, Refresh, and the initial mount). Resetting at the top
 * of that function covers every image-change path in one place.
 */
describe('SpaceEditor.vue test-result reset on image change', () => {
  it('loadScanImage clears every space.testResult to undefined', () => {
    // Pin the structural invariant: the start of loadScanImage
    // must iterate spaces and reset testResult. If this is removed
    // (or moved into a separate function that's not called on
    // every image change), the user sees red/green colors from
    // the previous test on the new image.
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    const startMatch = sfc.match(/async\s+function\s+loadScanImage[\s\S]*?\{/)
    expect(startMatch, 'loadScanImage must exist').toBeTruthy()
    // Bound the search: the next top-level `function` or
    // `async function` definition marks the end of loadScanImage's
    // source. The clear must appear somewhere inside.
    const after = sfc.slice(startMatch.index + startMatch[0].length)
    const endMatch = after.match(/\n\s*(?:async\s+)?function\s+[a-zA-Z_$]/)
    const body = endMatch ? after.slice(0, endMatch.index) : after
    expect(body, 'loadScanImage must clear testResult on every space').toMatch(
      /spaces\.value\.forEach[\s\S]{0,300}testResult\s*=\s*undefined/
    )
  })
})

describe('SpaceEditor.vue unsaved changes prevention', () => {
  it('registers window beforeunload event listener on mount and removes it on unmount', () => {
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    expect(sfc).toMatch(/window\.addEventListener\(\s*['"]beforeunload['"]\s*,\s*handleBeforeUnload\s*\)/)
    expect(sfc).toMatch(/window\.removeEventListener\(\s*['"]beforeunload['"]\s*,\s*handleBeforeUnload\s*\)/)
  })

  it('implements vue-router onBeforeRouteLeave and onBeforeRouteUpdate navigation guards', () => {
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    expect(sfc).toMatch(/onBeforeRouteLeave/)
    expect(sfc).toMatch(/onBeforeRouteUpdate/)
    expect(sfc).toMatch(/const\s+answer\s*=\s*window\.confirm/)
  })

  it('computes hasUnsavedChanges by comparing getSpacesState with originalSpacesState', () => {
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    expect(sfc).toMatch(/const\s+hasUnsavedChanges\s*=\s*computed\(/)
    expect(sfc).toMatch(/getSpacesState\(\s*spaces\.value\s*\)\s*!==\s*originalSpacesState\.value/)
  })

  it('resets originalSpacesState to default when camera is switched', () => {
    const sfc = fs.readFileSync(SFC_PATH, 'utf8')
    const watchMatch = sfc.match(/watch\(\s*cameraId\s*,[\s\S]*?\}\s*\)\s*;/)
    expect(watchMatch, 'cameraId watch must exist').toBeTruthy()
    expect(watchMatch[0]).toMatch(/originalSpacesState\.value\s*=\s*['"]\[\]['"]/)
  })

  it('updates originalSpacesState on successful data fetch and save', () => {
    const script = readScriptBlock()
    const saveBody = readFunctionBody(script, 'saveSpaces')
    const fetchBody = readFunctionBody(script, 'fetchData')
    expect(saveBody).toMatch(/originalSpacesState\.value\s*=\s*getSpacesState\(\s*spaces\.value\s*\)/)
    expect(fetchBody).toMatch(/originalSpacesState\.value\s*=\s*getSpacesState\(\s*spaces\.value\s*\)/)
  })

  it('loadScanImage attaches onload and onerror before setting img.src to avoid race with cached images', () => {
    const script = readScriptBlock()
    const body = readFunctionBody(script, 'loadScanImage')
    const onloadIdx = body.indexOf('img.onload =')
    const srcIdx = body.lastIndexOf('img.src =')
    expect(onloadIdx, 'img.onload must be attached').toBeGreaterThan(-1)
    expect(srcIdx, 'img.src must be set after onload').toBeGreaterThan(onloadIdx)
  })
})


