import 'package:flutter/material.dart';
import 'package:photo_view/photo_view.dart';
import 'package:photo_view/photo_view_gallery.dart';

import '../models.dart';

/// A snapshot of an alert's photos, so background updates cannot change the
/// photo being inspected. PhotoView coordinates paging and zoom gestures.
class WeaponPhotoGallery extends StatefulWidget {
  const WeaponPhotoGallery({
    super.key,
    required this.evidence,
    required this.initialIndex,
    this.onPageChanged,
  }) : assert(evidence.length > 0),
       assert(initialIndex >= 0 && initialIndex < evidence.length);

  final List<EvidenceRecord> evidence;
  final int initialIndex;
  final ValueChanged<int>? onPageChanged;

  @override
  State<WeaponPhotoGallery> createState() => _WeaponPhotoGalleryState();
}

class _WeaponPhotoGalleryState extends State<WeaponPhotoGallery> {
  late final PageController _pages;
  late final List<PhotoViewScaleStateController> _scales;
  late int _page;

  @override
  void initState() {
    super.initState();
    _page = widget.initialIndex;
    _pages = PageController(initialPage: _page);
    _scales = List.generate(
      widget.evidence.length,
      (_) => PhotoViewScaleStateController(),
    );
  }

  @override
  void dispose() {
    _pages.dispose();
    for (final controller in _scales) {
      controller.dispose();
    }
    super.dispose();
  }

  void _onPageChanged(int index) {
    _scales[_page].scaleState = PhotoViewScaleState.initial;
    _scales[index].scaleState = PhotoViewScaleState.initial;
    setState(() => _page = index);
    widget.onPageChanged?.call(index);
  }

  void _move(int direction) {
    final next = _page + direction;
    if (next < 0 || next >= widget.evidence.length) return;
    _scales[_page].scaleState = PhotoViewScaleState.initial;
    _pages.animateToPage(
      next,
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeOutCubic,
    );
  }

  @override
  Widget build(BuildContext context) {
    final count = widget.evidence.length;
    final photo = widget.evidence[_page];
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        leading: IconButton(
          tooltip: 'Close photos',
          onPressed: () => Navigator.of(context).pop(),
          icon: const Icon(Icons.close_rounded),
        ),
        centerTitle: true,
        title: Semantics(
          label: 'Photo ${_page + 1} of $count',
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(20),
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 6),
              child: Text(
                '${_page + 1} / $count',
                style: const TextStyle(
                  color: Color(0xFF111111),
                  fontSize: 14,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ),
          ),
        ),
        actions: [
          IconButton(
            tooltip: 'Reset zoom',
            onPressed: () =>
                _scales[_page].scaleState = PhotoViewScaleState.initial,
            icon: const Icon(Icons.zoom_out_map_rounded),
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: [
            Expanded(
              child: PhotoViewGallery.builder(
                key: const ValueKey('weapon-photo-gallery'),
                pageController: _pages,
                itemCount: count,
                onPageChanged: _onPageChanged,
                scrollPhysics: const BouncingScrollPhysics(),
                backgroundDecoration: const BoxDecoration(color: Colors.black),
                loadingBuilder: (_, _) => const Center(
                  child: CircularProgressIndicator(color: Colors.white70),
                ),
                builder: (context, index) {
                  final item = widget.evidence[index];
                  if (item.url == null) {
                    return PhotoViewGalleryPageOptions.customChild(
                      child: const _PhotoUnavailable(),
                      disableGestures: true,
                    );
                  }
                  return PhotoViewGalleryPageOptions(
                    key: ValueKey('weapon-photo-${item.id}'),
                    imageProvider: NetworkImage(
                      item.url!,
                      headers: item.httpHeaders,
                    ),
                    semanticLabel:
                        'Weapon evidence photo ${index + 1} of $count',
                    initialScale: PhotoViewComputedScale.contained,
                    minScale: PhotoViewComputedScale.contained,
                    maxScale: PhotoViewComputedScale.contained * 6,
                    scaleStateController: _scales[index],
                    errorBuilder: (_, _, _) => const _PhotoUnavailable(),
                  );
                },
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(8, 8, 8, 12),
              child: Row(
                children: [
                  if (count > 1)
                    IconButton(
                      tooltip: 'Previous photo',
                      color: Colors.white,
                      disabledColor: Colors.white24,
                      onPressed: _page > 0 ? () => _move(-1) : null,
                      icon: const Icon(Icons.chevron_left_rounded),
                    ),
                  Expanded(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (photo.startSeconds != null)
                          Text(
                            'Source ${photo.startSeconds!.toStringAsFixed(1)}s',
                            style: const TextStyle(color: Colors.white70),
                          ),
                        Text(
                          count > 1
                              ? 'Swipe for more photos · Pinch or double-tap to zoom'
                              : 'Pinch or double-tap to zoom',
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: Colors.white70,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ),
                  ),
                  if (count > 1)
                    IconButton(
                      tooltip: 'Next photo',
                      color: Colors.white,
                      disabledColor: Colors.white24,
                      onPressed: _page < count - 1 ? () => _move(1) : null,
                      icon: const Icon(Icons.chevron_right_rounded),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _PhotoUnavailable extends StatelessWidget {
  const _PhotoUnavailable();

  @override
  Widget build(BuildContext context) => const Center(
    child: Padding(
      padding: EdgeInsets.all(24),
      child: Text(
        'Unable to load this photo. You can still view the other photos.',
        textAlign: TextAlign.center,
        style: TextStyle(color: Colors.white70),
      ),
    ),
  );
}
