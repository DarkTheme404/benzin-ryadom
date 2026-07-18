import 'dart:async';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';
import '../config/theme.dart';
import '../models/station.dart';
import '../services/api_service.dart';
import '../services/location_service.dart';
import '../widgets/station_bottom_sheet.dart';

class MapScreen extends StatefulWidget {
  const MapScreen({super.key});

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  final MapController _mapController = MapController();
  final LocationService _locationService = LocationService();
  final ApiService _api = ApiService();

  List<Station> _stations = [];
  bool _isLoading = true;
  String _selectedFuel = '95';
  Station? _selectedStation;
  bool _showSheet = false;
  LatLng? _userLocation;

  static const LatLng _defaultCenter = LatLng(56.8587, 40.9957);

  @override
  void initState() {
    super.initState();
    _initLocation();
  }

  Future<void> _initLocation() async {
    final pos = await _locationService.getCurrentPosition();
    if (pos != null) {
      _userLocation = LatLng(pos.latitude, pos.longitude);
      _loadStations(pos.latitude, pos.longitude);
    } else {
      _loadStations(_defaultCenter.latitude, _defaultCenter.longitude);
    }
  }

  Future<void> _loadStations(double lat, double lon) async {
    setState(() => _isLoading = true);
    try {
      final stations = await _api.getStations(
        lat: lat,
        lon: lon,
        fuel: _selectedFuel,
      );
      setState(() {
        _stations = stations;
        _isLoading = false;
      });
    } catch (_) {
      setState(() => _isLoading = false);
    }
  }

  void _onMapEvent(MapEvent event) {
    final center = event.camera.center;
    _loadStations(center.latitude, center.longitude);
  }

  void _onStationTap(Station station) {
    setState(() {
      _selectedStation = station;
      _showSheet = true;
    });
  }

  void _onFuelChanged(String fuel) {
    setState(() => _selectedFuel = fuel);
    _initLocation();
  }

  Color _markerColor(String status) {
    switch (status) {
      case 'in_stock':
        return const Color(0xFF22c55e);
      case 'partial':
        return const Color(0xFFeab308);
      case 'out_of_stock':
        return const Color(0xFFef4444);
      default:
        return const Color(0xFF6b7280);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          FlutterMap(
            mapController: _mapController,
            options: MapOptions(
              initialCenter: _userLocation ?? _defaultCenter,
              initialZoom: 12,
              onMapEvent: _onMapEvent,
              interactionOptions: const InteractionOptions(
                flags: InteractiveFlag.all & ~InteractiveFlag.rotate,
              ),
            ),
            children: [
              TileLayer(
                urlTemplate: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
                subdomains: const ['a', 'b', 'c'],
                userAgentPackageName: 'com.benzinryadom.app',
              ),
              MarkerLayer(markers: _buildMarkers()),
              if (_userLocation != null)
                MarkerLayer(markers: [_buildUserMarker()]),
            ],
          ),
          if (_isLoading)
            const Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: LinearProgressIndicator(
                backgroundColor: Colors.transparent,
                valueColor: AlwaysStoppedAnimation(AppTheme.accent),
              ),
            ),
          Positioned(
            top: MediaQuery.of(context).padding.top + 8,
            left: 16,
            right: 16,
            child: _buildFuelChips(),
          ),
          Positioned(
            bottom: _showSheet ? 280 : 16,
            right: 16,
            child: _buildLocationButton(),
          ),
          if (_showSheet && _selectedStation != null)
            Positioned(
              bottom: 0,
              left: 0,
              right: 0,
              child: StationBottomSheet(
                station: _selectedStation!,
                onClose: () => setState(() {
                  _showSheet = false;
                  _selectedStation = null;
                }),
              ),
            ),
        ],
      ),
    );
  }

  List<Marker> _buildMarkers() {
    return _stations.where((s) => s.lat != null && s.lon != null).map((station) {
      final color = _markerColor(station.fuelStatus);

      return Marker(
        point: LatLng(station.lat!, station.lon!),
        width: 36,
        height: 44,
        child: GestureDetector(
          onTap: () => _onStationTap(station),
          child: _StationMarker(
            color: color,
            price: station.mainPrice,
          ),
        ),
      );
    }).toList();
  }

  Marker _buildUserMarker() {
    return Marker(
      point: _userLocation!,
      width: 24,
      height: 24,
      child: const _UserLocationMarker(),
    );
  }

  Widget _buildFuelChips() {
    return Container(
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: AppTheme.bg,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppTheme.divider),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _fuelChip('92', 'АИ-92'),
          _fuelChip('95', 'АИ-95'),
          _fuelChip('98', 'АИ-98'),
          _fuelChip('diesel', 'ДТ'),
          _fuelChip('lpg', 'Газ'),
        ],
      ),
    );
  }

  Widget _fuelChip(String value, String label) {
    final isSelected = _selectedFuel == value;
    return GestureDetector(
      onTap: () => _onFuelChanged(value),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: isSelected ? AppTheme.accent : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: isSelected ? Colors.white : AppTheme.textSecondary,
            fontSize: 12,
            fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }

  Widget _buildLocationButton() {
    return FloatingActionButton(
      mini: true,
      backgroundColor: AppTheme.bgCard,
      onPressed: () async {
        final pos = await _locationService.getCurrentPosition();
        if (pos != null) {
          final loc = LatLng(pos.latitude, pos.longitude);
          setState(() => _userLocation = loc);
          _mapController.move(loc, 14);
        }
      },
      child: const Icon(Icons.my_location, color: AppTheme.accent),
    );
  }
}

class _StationMarker extends StatelessWidget {
  final Color color;
  final String? price;

  const _StationMarker({required this.color, this.price});

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          constraints: const BoxConstraints(minWidth: 40),
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(4),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.3),
                blurRadius: 4,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: Text(
            price ?? '⛽',
            style: const TextStyle(
              color: Colors.white,
              fontSize: 10,
              fontWeight: FontWeight.w700,
            ),
            textAlign: TextAlign.center,
          ),
        ),
        CustomPaint(
          size: const Size(12, 8),
          painter: _TrianglePainter(color: color),
        ),
      ],
    );
  }
}

class _TrianglePainter extends CustomPainter {
  final Color color;
  _TrianglePainter({required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = color;
    final path = ui.Path()
      ..moveTo(0, 0)
      ..lineTo(size.width, 0)
      ..lineTo(size.width / 2, size.height)
      ..close();
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _UserLocationMarker extends StatefulWidget {
  const _UserLocationMarker();

  @override
  State<_UserLocationMarker> createState() => _UserLocationMarkerState();
}

class _UserLocationMarkerState extends State<_UserLocationMarker>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;
  late Animation<double> _anim;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 2),
    )..repeat(reverse: false);
    _anim = Tween<double>(begin: 0.3, end: 0.0).animate(
      CurvedAnimation(parent: _controller, curve: Curves.easeOut),
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _anim,
      builder: (_, __) {
        return Stack(
          alignment: Alignment.center,
          children: [
            Container(
              width: 20 + (1 - _anim.value) * 16,
              height: 20 + (1 - _anim.value) * 16,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: AppTheme.info.withValues(alpha: _anim.value),
              ),
            ),
            Container(
              width: 14,
              height: 14,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: AppTheme.info,
                border: Border.all(color: Colors.white, width: 3),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withValues(alpha: 0.3),
                    blurRadius: 4,
                  ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}
