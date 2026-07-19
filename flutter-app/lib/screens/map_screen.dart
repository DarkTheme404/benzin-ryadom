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
  Timer? _debounce;
  bool _locationError = false;

  static const LatLng _defaultCenter = LatLng(56.8587, 40.9957);

  @override
  void initState() {
    super.initState();
    _initLocation();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }

  Future<void> _initLocation() async {
    final pos = await _locationService.getCurrentPosition();
    if (pos != null) {
      setState(() {
        _userLocation = LatLng(pos.latitude, pos.longitude);
        _locationError = false;
      });
      _loadStations(pos.latitude, pos.longitude);
    } else {
      setState(() => _locationError = true);
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
      if (mounted) {
        setState(() {
          _stations = stations;
          _isLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => _isLoading = false);
      }
    }
  }

  void _onMapEvent(MapEvent event) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 800), () {
      final center = event.camera.center;
      _loadStations(center.latitude, center.longitude);
    });
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
      case 'available':
        return const Color(0xFF22c55e);
      case 'partial':
        return const Color(0xFFeab308);
      case 'unavailable':
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
                urlTemplate:
                    'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
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
          if (_locationError)
            Positioned(
              top: MediaQuery.of(context).padding.top + 8,
              left: 50,
              right: 50,
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                decoration: BoxDecoration(
                  color: AppTheme.warning.withValues(alpha: 0.9),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Text(
                  '📍 Геолокация недоступна. Выбери город вручную.',
                  style: TextStyle(color: Colors.white, fontSize: 12),
                  textAlign: TextAlign.center,
                ),
              ),
            ),
          Positioned(
            top: MediaQuery.of(context).padding.top +
                (_locationError ? 48 : 8),
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
    return _stations
        .where((s) => s.lat != null && s.lon != null)
        .map((station) {
      final status = station.fuelStatusForType(_selectedFuel);
      final color = _markerColor(status);

      return Marker(
        point: LatLng(station.lat!, station.lon!),
        width: 44,
        height: 52,
        child: GestureDetector(
          onTap: () => _onStationTap(station),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                constraints: const BoxConstraints(minWidth: 44),
                padding:
                    const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
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
                  station.mainPrice ?? '⛽',
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 9,
                    fontWeight: FontWeight.w700,
                  ),
                  textAlign: TextAlign.center,
                ),
              ),
              CustomPaint(
                size: const Size(10, 6),
                painter: _TrianglePainter(color: color),
              ),
            ],
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
      child: Container(
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
          setState(() {
            _userLocation = loc;
            _locationError = false;
          });
          _mapController.move(loc, 14);
        }
      },
      child: const Icon(Icons.my_location, color: AppTheme.accent),
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
